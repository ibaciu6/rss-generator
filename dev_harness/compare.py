#!/usr/bin/env python3
"""
Comparison logic for run-to-run and stage-to-stage diffs.
"""
from __future__ import annotations

import difflib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FeedDiff:
    """Differences between two versions of a feed."""
    feed_name: str
    items_added: list[dict] = None
    items_removed: list[dict] = None
    items_reordered: bool = False
    field_changes: dict = None
    xml_diff: str = ""
    summary: str = ""


@dataclass
class SiteVerdict:
    """Verdict for a site comparison against baseline."""
    site_name: str
    category: str
    verdict: str  # PASS, WARN, FAIL
    metrics: dict = None
    issues: list[str] = None


def compare_runs(run_a: Path, run_b: Path, sites_filter: str | None = None) -> int:
    """Compare two run directories and produce a comparison report."""
    print(f"Comparing {run_a.name} vs {run_b.name}")

    # Load manifests
    manifest_a = load_manifest(run_a)
    manifest_b = load_manifest(run_b)

    # Determine sites to compare
    if sites_filter:
        sites = [s.strip() for s in sites_filter.split(",")]
    else:
        sites = sorted(set(manifest_a.sites_selected) | set(manifest_b.sites_selected))

    # Compare each stage
    stages = ["generate", "enrich", "fix", "index", "reader"]
    all_diffs = {}

    for site in sites:
        feed_file = f"{site}.xml" if not site.endswith(".xml") else site
        print(f"\n--- {feed_file} ---")
        site_diffs = {}

        for stage in stages:
            stage_a = get_stage_dir(run_a, stage)
            stage_b = get_stage_dir(run_b, stage)
            feed_a = stage_a / feed_file
            feed_b = stage_b / feed_file

            if feed_a.exists() and feed_b.exists():
                diff = compare_feed_files(feed_a, feed_b)
                if diff and (diff.items_added or diff.items_removed or diff.field_changes):
                    site_diffs[stage] = diff
                    print(f"  {stage}: {len(diff.items_added)} added, {len(diff.items_removed)} removed")
            elif feed_a.exists() and not feed_b.exists():
                print(f"  {stage}: REMOVED in run B")
            elif not feed_a.exists() and feed_b.exists():
                print(f"  {stage}: ADDED in run B")

        if site_diffs:
            all_diffs[site] = site_diffs

    # Generate comparison report
    report_dir = run_b / "compare" / run_a.name
    report_dir.mkdir(parents=True, exist_ok=True)

    with (report_dir / "comparison.json").open("w") as f:
        json.dump({
            "run_a": run_a.name,
            "run_b": run_b.name,
            "sites_compared": sites,
            "diffs": {s: {st: asdict(d) for st, d in stages.items()} for s, stages in all_diffs.items()},
        }, f, indent=2, default=str)

    print(f"\nComparison report written to {report_dir}")
    return 0


def load_manifest(run_dir: Path) -> Any:
    """Load run manifest."""
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open() as f:
            data = json.load(f)
        # Convert to simple object
        class Manifest:
            pass
        m = Manifest()
        for k, v in data.items():
            setattr(m, k, v)
        return m
    return None


def get_stage_dir(run_dir: Path, stage: str) -> Path:
    """Get stage directory path."""
    stage_map = {
        "crawl": "00_crawl",
        "generate": "01_generate",
        "enrich": "02_enrich",
        "fix": "03_fix",
        "index": "04_index",
        "reader": "05_reader",
    }
    return run_dir / stage_map.get(stage, stage)


def compare_feed_files(file_a: Path, file_b: Path) -> FeedDiff:
    """Compare two RSS feed XML files."""
    diff = FeedDiff(feed_name=file_a.name)
    diff.items_added = []
    diff.items_removed = []
    diff.field_changes = {}

    try:
        tree_a = ET.parse(file_a)
        tree_b = ET.parse(file_b)
        root_a = tree_a.getroot()
        root_b = tree_b.getroot()

        items_a = parse_items(root_a)
        items_b = parse_items(root_b)

        # Compare by GUID or link
        by_guid_a = {item.get("guid", item["link"]): item for item in items_a}
        by_guid_b = {item.get("guid", item["link"]): item for item in items_b}

        guids_a = set(by_guid_a.keys())
        guids_b = set(by_guid_b.keys())

        added_guids = guids_b - guids_a
        removed_guids = guids_a - guids_b
        common_guids = guids_a & guids_b

        for guid in added_guids:
            diff.items_added.append(by_guid_b[guid])
        for guid in removed_guids:
            diff.items_removed.append(by_guid_a[guid])

        # Compare common items field by field
        for guid in common_guids:
            item_a = by_guid_a[guid]
            item_b = by_guid_b[guid]
            field_diffs = {}
            for field in ["title", "link", "description", "pubDate", "guid"]:
                if item_a.get(field) != item_b.get(field):
                    field_diffs[field] = {"old": item_a.get(field), "new": item_b.get(field)}
            if field_diffs:
                diff.field_changes[guid] = field_diffs

        # Generate XML diff
        xml_a = ET.tostring(root_a, encoding="unicode")
        xml_b = ET.tostring(root_b, encoding="unicode")
        diff.xml_diff = "\n".join(difflib.unified_diff(
            xml_a.splitlines(), xml_b.splitlines(),
            fromfile=str(file_a), tofile=str(file_b),
            lineterm=""
        ))

        # Summary
        diff.summary = f"Items: +{len(diff.items_added)}/-{len(diff.items_removed)}, Fields changed: {len(diff.field_changes)}"

    except Exception as e:
        diff.summary = f"Error comparing feeds: {e}"

    return diff


def parse_items(root: ET.Element) -> list[dict]:
    """Parse items from RSS/Atom feed."""
    items = []
    # Try RSS first
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            parsed = {}
            for child in item:
                tag = child.tag
                if "}" in tag:
                    tag = tag.split("}")[1]
                parsed[tag] = child.text
            items.append(parsed)
        return items

    # Try Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f"{ns}entry"):
        parsed = {}
        for child in entry:
            tag = child.tag
            if tag.startswith(ns):
                tag = tag[len(ns):]
            parsed[tag] = child.text
        items.append(parsed)
    return items


def asdict(obj) -> dict:
    """Convert dataclass to dict."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: asdict(v) for k, v in obj.__dict__.items()}
    elif isinstance(obj, dict):
        return {k: asdict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [asdict(v) for v in obj]
    return obj