#!/usr/bin/env python3
"""Run the full fetch pipeline for one or more specific feeds.

The normal pipeline is three separate stages -- generate, enrich, fix -- and each
sweep rewrites every feed it is given. That makes iterating on a single site
slow, and worse: a bare ``generate`` silently discards the previous run's
enrichment, so a blog feed reverts to its bare RSS excerpt until ``enrich`` is
run again. This script runs all three stages against a named subset, in the same
order CI uses, and then reports how much body text each item actually ended up
with so a truncated article is obvious immediately.

    python scripts/refresh_feed.py gabriel-ursan
    python scripts/refresh_feed.py --site showrss --site ghacks

``index.html`` / ``feeds.opml`` are deliberately NOT rebuilt: they are global
derived artifacts covering every feed, and the local reader reads ``feeds/``
directly. Use ``./start.sh index`` when the site list itself changed.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

# Running this file directly puts scripts/ on sys.path instead of the repo
# root, so `from core...` would fail. Fix the path before the project imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cli import main as cli_main
from core.config import load_config, resolve_feed_files
from scripts.enrich_feeds import main as enrich_main
from scripts.fix_feeds import main as fix_main

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDS_DIR = REPO_ROOT / "feeds"
CONFIG_FILE = REPO_ROOT / "config" / "sites.yaml"
CACHE_FILE = REPO_ROOT / "data" / "cache.json"

STAGES = ("get", "enrich", "process")


def _text_len(html_text: str) -> int:
    """Length of an item's description once tags are stripped."""
    stripped = re.sub(r"<[^>]+>", " ", html_text or "")
    return len(re.sub(r"\s+", " ", unescape(stripped)).strip())


def _report(feed_files: list[str]) -> None:
    """Print per-item body length for each refreshed feed.

    A blog/news item whose body is only a few hundred characters is showing the
    site's own RSS excerpt rather than the fetched article, which is the failure
    this whole script exists to make visible.
    """
    print()
    print("  Body text per item (chars):")
    for feed_file in feed_files:
        path = FEEDS_DIR / feed_file
        if not path.exists():
            print(f"    {feed_file}: MISSING")
            continue
        try:
            channel = ET.parse(path).getroot().find("channel")
        except ET.ParseError as exc:
            print(f"    {feed_file}: unparseable XML ({exc})")
            continue
        if channel is None:
            print(f"    {feed_file}: no <channel>")
            continue

        items = channel.findall("item")
        if not items:
            print(f"    {feed_file}: no items")
            continue

        lengths = []
        for item in items:
            desc = item.find("description")
            lengths.append(_text_len(desc.text if desc is not None else ""))

        thin = sum(1 for n in lengths if n < 800)
        flag = "" if thin == 0 else f"   <-- {thin} item(s) look like bare excerpts"
        print(
            f"    {feed_file}: {len(items)} items, "
            f"min={min(lengths)} max={max(lengths)} avg={sum(lengths) // len(lengths)}{flag}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="refresh_feed",
        description="Run get -> enrich -> process for specific feeds only.",
        epilog="Example: python scripts/refresh_feed.py gabriel-ursan",
    )
    parser.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="Feed(s) to refresh, as bare names. Same as repeating --site.",
    )
    parser.add_argument(
        "--site",
        dest="sites",
        action="append",
        metavar="NAME",
        help=(
            "Feed to refresh. Matches the site `name` or its feed_file, with or without "
            ".xml. Repeatable. Required."
        ),
    )
    parser.add_argument(
        "--skip",
        dest="skip",
        action="append",
        choices=STAGES,
        metavar="STAGE",
        help=f"Skip a stage ({', '.join(STAGES)}). Repeatable.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip the per-item body-length report at the end.",
    )
    # argv or [] rather than argv: a programmatic main() call must never pick up
    # the *process* arguments, which is what argparse's None default would do.
    args = parser.parse_args(argv or [])

    # Both spellings are accepted because `./start.sh one ghacks` should just
    # work, while `--site` stays available for names argparse would misread.
    sites = list(args.names) + list(args.sites or [])
    if not sites:
        parser.error("at least one site is required (this script never sweeps every feed; use ./start.sh all)")
    skip = set(args.skip or [])

    # Validate before doing any work, so a typo costs nothing.
    config = load_config(CONFIG_FILE)
    feed_files, unmatched = resolve_feed_files(config, sites)
    for name in unmatched:
        print(f"ERROR unknown site: {name}", file=sys.stderr)
    if unmatched or not feed_files:
        return 1

    by_feed = {site.feed_file: site for site in config.sites}
    disabled = sorted(f for f in feed_files if not by_feed[f].enabled)
    if disabled:
        print(
            f"ERROR these sites are disabled in config/sites.yaml and will not generate: "
            f"{', '.join(disabled)}",
            file=sys.stderr,
        )
        return 1

    print(f"Refreshing {len(feed_files)} feed(s): {', '.join(sorted(feed_files))}")
    print()

    stages = [
        ("get", "scrape + write RSS", lambda: cli_main(
            ["generate", "--config", str(CONFIG_FILE), "--cache", str(CACHE_FILE),
             "--feeds-dir", str(FEEDS_DIR), "--site", *sites]
        )),
        ("enrich", "posters / links / article bodies", lambda: asyncio.run(
            enrich_main(["--site", *sites])
        )),
        ("process", "year + poster + link fixes", lambda: fix_main(["--site", *sites])),
    ]

    failed: list[str] = []
    for name, blurb, run in stages:
        if name in skip:
            print(f"  [{name:<7}] skipped")
            continue
        print(f"  [{name:<7}] {blurb}...")
        started = time.monotonic()
        try:
            rc = run()
        except Exception as exc:  # a broken stage must not hide the later ones
            print(f"  [{name:<7}] FAILED after {time.monotonic() - started:.1f}s: "
                  f"{type(exc).__name__}: {exc}")
            failed.append(name)
            continue
        elapsed = time.monotonic() - started
        if rc != 0:
            print(f"  [{name:<7}] FAILED (exit {rc}) after {elapsed:.1f}s")
            failed.append(name)
        else:
            print(f"  [{name:<7}] done in {elapsed:.1f}s")

    if not args.no_report:
        _report(sorted(feed_files))

    if failed:
        print(f"\nFAILED stages: {', '.join(failed)}")
        return 1
    print("\nFull flow complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
