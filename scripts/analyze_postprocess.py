#!/usr/bin/env python3
"""Measure which post-processing steps actually do something, per feed.

The fix stage runs eight independent repairs over every item. Most feeds need a
few of them and no others, and running the rest costs time and -- worse -- makes
the next change harder to reason about. This tool measures, from a real
pipeline snapshot, which steps change an item's text and which are no-ops:

* **titles**: does the title pass rewrite any title?
* **every other step**: run the whole description pipeline, then run it again
  with that one step removed, and compare. If the output is byte-identical the
  step is unnecessary *for that feed on this snapshot*.

It also compares consecutive snapshots (generate -> enrich -> fix) so the
enrichment question ("does this feed need enriching at all?") is answered from
the same evidence, not from a guess.

    PYTHONPATH=. python scripts/analyze_postprocess.py
    PYTHONPATH=. python scripts/analyze_postprocess.py --snapshot logs/pipeline/2-enrich
    PYTHONPATH=. python scripts/analyze_postprocess.py --json logs/postprocess.json

The snapshot is pre-fix (`2-enrich`), which is the input the fix stage really
sees. Nothing is written into ``feeds/``.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from scripts.enrichers.postprocess_steps import STEP_NAMES, resolve_steps
from scripts.fix_feeds import FEED_CONTENT_MODULES, apply_description_passes, fix_title

REPO = Path(__file__).resolve().parent.parent
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"


@dataclass(frozen=True)
class Item:
    title: str
    link: str
    pub_date: str | None
    description: str


def read_feed(path: Path) -> list[Item]:
    """Read the items of one feed, matching what ``fix_feeds.process_feed`` sees."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    channel = root.find("channel")
    items: list[Item] = []
    for item in (channel if channel is not None else root).findall("item"):
        el = item.find("description")
        if el is None or not el.text:
            el = item.find(CONTENT_NS)
        if el is None or not el.text:
            continue
        items.append(
            Item(
                title=(item.findtext("title") or "").strip(),
                link=item.findtext("link") or "",
                pub_date=item.findtext("pubDate"),
                description=el.text,
            )
        )
    return items


def _run(item: Item, feed: str, title: str, steps: tuple[str, ...]) -> str:
    return apply_description_passes(
        item.description,
        feed,
        title=title,
        link=item.link,
        pub_date=item.pub_date,
        steps=steps,
    )


def measure_feed(path: Path) -> dict:
    """Which steps change this feed's items, and how many items each one touches."""
    feed = path.name
    items = read_feed(path)
    changed_by: dict[str, int] = {name: 0 for name in STEP_NAMES}
    untouched = 0

    for item in items:
        title = fix_title(item.title, feed, item.link, steps=STEP_NAMES)
        if title != item.title:
            changed_by["titles"] += 1

        baseline = _run(item, feed, title, STEP_NAMES)
        if baseline == item.description:
            untouched += 1
            continue
        for step in STEP_NAMES:
            if step == "titles":
                continue
            without = tuple(name for name in STEP_NAMES if name != step)
            if _run(item, feed, title, without) != baseline:
                changed_by[step] += 1

    needed = tuple(name for name in STEP_NAMES if changed_by[name])
    return {
        "feed": feed,
        "items": len(items),
        "untouched": untouched,
        "changed_by": changed_by,
        "needed": needed,
        "content_modules": list(FEED_CONTENT_MODULES.get(feed) or ()),
    }


def describe_stage_delta(before: Path, after: Path) -> dict[str, int]:
    """How many items per feed changed between two snapshots (raw text)."""
    out: dict[str, int] = {}
    for path in sorted(before.glob("*.xml")):
        other = after / path.name
        if not other.exists():
            continue
        old = {i.title: i.description for i in read_feed(path)}
        new = {i.title: i.description for i in read_feed(other)}
        shared = old.keys() & new.keys()
        out[path.name] = sum(1 for title in shared if old[title] != new[title])
    return out


def _flag(count: int) -> str:
    return f"{count:>3}" if count else "  ."


def report(rows: list[dict], enrich_delta: dict[str, int]) -> None:
    width = max(len(row["feed"]) for row in rows) if rows else 10
    header = f"{'feed':<{width}}  items  " + "  ".join(f"{n[:8]:>8}" for n in STEP_NAMES)
    print(header)
    print("-" * len(header))
    for row in rows:
        cells = "  ".join(f"{_flag(row['changed_by'][n]):>8}" for n in STEP_NAMES)
        print(f"{row['feed']:<{width}}  {row['items']:>5}  {cells}")

    whole_stage_noop = [r for r in rows if not r["needed"]]
    partial = [r for r in rows if r["needed"] and len(r["needed"]) < len(STEP_NAMES)]

    print()
    print("== per-feed overrides ==")
    if whole_stage_noop:
        print("feeds where the fix stage does nothing at all (postprocess: []):")
        for row in whole_stage_noop:
            print(f"  {row['feed']}")
    if partial:
        print("feeds where only some steps do something:")
        for row in partial:
            names = ", ".join(row["needed"])
            print(f"  {row['feed']}: postprocess: [{names}]")
    if not whole_stage_noop and not partial:
        print("  none: every feed uses every step")

    no_enrichment = sorted(feed for feed, changed in enrich_delta.items() if not changed)
    if no_enrichment:
        print()
        print("== no enrichment change on this snapshot (enhance_mode: none candidates) ==")
        for feed in no_enrichment:
            sites = [r for r in rows if r["feed"] == feed]
            modules = sites[0]["content_modules"] if sites else []
            print(f"  {feed}  (content_modules: {modules or 'none'})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--snapshot",
        default="logs/pipeline/2-enrich",
        help="pre-fix snapshot to measure (default: logs/pipeline/2-enrich)",
    )
    ap.add_argument(
        "--enrich-base",
        default="logs/pipeline/1-generate",
        help="snapshot before enrichment, for the no-enrichment candidates",
    )
    ap.add_argument("--feeds", nargs="*", help="restrict to these feed files")
    ap.add_argument("--json", help="also write the measurements here")
    args = ap.parse_args(argv)

    snapshot = REPO / args.snapshot
    if not snapshot.is_dir():
        print(f"no snapshot at {snapshot}; run scripts/compare_pipeline.py --run first")
        return 1

    paths = sorted(snapshot.glob("*.xml"))
    if args.feeds:
        wanted = set(args.feeds)
        paths = [p for p in paths if p.name in wanted]
    if not paths:
        print(f"no feeds to measure in {snapshot}")
        return 1

    enrich_base = REPO / args.enrich_base
    enrich_delta = (
        describe_stage_delta(enrich_base, snapshot) if enrich_base.is_dir() else {}
    )

    rows = [measure_feed(path) for path in paths]
    report(rows, enrich_delta)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "snapshot": str(snapshot.relative_to(REPO)),
                    "steps": list(STEP_NAMES),
                    "feeds": rows,
                    "enrichment_changes": enrich_delta,
                    "default_steps": list(resolve_steps(None)),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
