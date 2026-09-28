#!/usr/bin/env python3
"""Why did an article item keep its RSS excerpt instead of its full body?

A feed can be "enriched" -- the run reports success -- while every single item
fell back to the two-paragraph excerpt the site shipped in its own RSS. Nothing
logs that, so the symptom is indistinguishable from a feed that is simply
short. This walks the real pipeline over real pages and reports, per item,
exactly which gate stopped it.

The gates, in the order article_enricher applies them:

  fetch-failed    the page did not return 200
  challenge       looks_like_challenge() refused it -- a 200 that is not the
                  article (a Cloudflare interstitial sails past the emptiness
                  check, which is why it needs its own test)
  no-extraction   extract_main_content() found nothing usable
  too-short       visible_text_length() < MIN_BODY_TEXT
  too-long        over MAX_DESCRIPTION_LENGTH after truncation

Each item is also measured against the site's own `detail_article_selector` and
against the generic path, so "we have no selector for this site" is
distinguishable from "the selector is stale".

Read-only: fetches article pages, writes nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import httpx

from core.config import load_config
from scripts.enrichers.ad_remover import extract_main_content
from scripts.enrichers.article_enricher import (
    MAX_DESCRIPTION_LENGTH,
    MIN_BODY_TEXT,
    looks_like_challenge,
    visible_text_length,
)
from scripts.enrichers.removal_modules import apply_modules

STRIP = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def feed_text(html: str) -> int:
    return len(WS.sub(" ", TAGS.sub(" ", STRIP.sub(" ", html or ""))).strip())


def classify(page: str | None, site_selector: str | None, removals: list[str]) -> tuple[str, int, int]:
    """(verdict, generic_body_len, selector_body_len) for one fetched page."""
    if not page:
        return "fetch-failed", 0, 0
    if looks_like_challenge(page):
        return "challenge", 0, 0

    def measure(selectors: list[str] | None) -> tuple[str, int]:
        try:
            html = extract_main_content(page, article_selectors=selectors)
        except Exception:
            return "", 0
        if removals:
            html = apply_modules(html, removals)
        return html, visible_text_length(html)

    _generic_html, generic_len = measure(None)
    sel_len = 0
    if site_selector:
        _sel_html, sel_len = measure([site_selector])

    best = max(generic_len, sel_len)
    if not best:
        return "no-extraction", generic_len, sel_len
    if best < MIN_BODY_TEXT:
        return f"too-short (best {best}, floor {MIN_BODY_TEXT})", generic_len, sel_len
    if best > MAX_DESCRIPTION_LENGTH:
        return "too-long", generic_len, sel_len
    return "ok", generic_len, sel_len


async def run(args) -> int:
    cfg = load_config(Path("config/sites.yaml"))
    by_file = {s.feed_file: s for s in cfg.sites if s.feed_file}
    targets = args.feeds or sorted(p.name for p in Path(args.feeds_dir).glob("*.xml"))

    report: dict[str, Counter] = {}
    detail: list[str] = []
    async with httpx.AsyncClient(
        timeout=args.timeout, follow_redirects=True, headers={"User-Agent": args.user_agent}
    ) as client:
        for feed_file in targets:
            path = Path(args.feeds_dir) / feed_file
            if not path.is_file():
                continue
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue
            channel = root.find("channel")
            items = (channel if channel is not None else root).findall("item")
            site = by_file.get(feed_file)
            selector = getattr(site, "detail_article_selector", None)
            removals = list(getattr(site, "removals", None) or [])
            counts: Counter = Counter()
            n = 0
            for item in items[: args.limit]:
                link_el = item.find("link")
                desc_el = item.find("description")
                have = feed_text(desc_el.text if desc_el is not None else "")
                if not link_el is not None or not link_el.text:
                    counts["no-link"] += 1
                    continue
                n += 1
                try:
                    resp = await client.get(link_el.text.strip())
                    page = resp.text if resp.status_code == 200 else None
                except Exception:
                    page = None
                verdict, glen, slen = classify(page, selector, removals)
                counts[verdict.split(" ")[0]] += 1
                if args.verbose and verdict != "ok":
                    detail.append(
                        f"  {feed_file:34} have={have:5} generic={glen:6} selector={slen:6}  {verdict}"
                    )
                elif args.verbose and verdict == "ok" and have < args.short:
                    detail.append(
                        f"  {feed_file:34} have={have:5} generic={glen:6} selector={slen:6}  ok"
                    )
            if n:
                report[feed_file] = counts

    print(f'{"feed":36} {"items":>5} {"ok":>5} {"selector":>8}  failing verdicts')
    print("-" * 104)
    for feed_file, counts in sorted(report.items()):
        site = by_file.get(feed_file)
        sel = getattr(site, "detail_article_selector", None)
        total = sum(counts.values())
        bad = {k: v for k, v in counts.items() if k != "ok"}
        print(
            f"{feed_file:36} {total:5} {counts['ok']:5} {('yes' if sel else 'NO'):>8}  "
            + (", ".join(f"{k}={v}" for k, v in sorted(bad.items())) or "-")
        )
    if detail:
        print()
        print("\n".join(detail))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("feeds", nargs="*", help="feed file names, e.g. hackread.xml")
    ap.add_argument("--feeds-dir", default="feeds")
    ap.add_argument("--limit", type=int, default=3, help="items per feed")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--short", type=int, default=800, help="report ok items already this short")
    ap.add_argument("--user-agent", default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
    ap.add_argument("-v", "--verbose", action="store_true")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
