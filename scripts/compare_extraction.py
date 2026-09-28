#!/usr/bin/env python3
"""Compare article extraction, candidate-first vs candidate-largest, on real pages.

`extract_main_content` used to take `elements[0]` -- the first element a
selector matched -- which silently returns a sidebar card when a page has
several elements the selector fits. The fix picks the candidate holding the
most visible text. That is obviously right in the case that prompted it, and
"obviously right" is exactly what a change to the extraction path deserves to
be suspicious of, so this measures both across every article feed.

Prints one line per feed with the before/after visible-character totals and
the per-item direction, so a regression is visible as a `worse` count rather
than as an aggregate that happens to look fine.

    PYTHONPATH=. python scripts/compare_extraction.py
    PYTHONPATH=. python scripts/compare_extraction.py --site hackread
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.enrichers.ad_remover import (
    DEFAULT_AD_SELECTORS,
    _truncate_at_footer,
    extract_main_content,
)
from scripts.enrichers.article_enricher import (
    MIN_BODY_TEXT,
    _fetch_article_page,
    looks_like_challenge,
)

TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def text_len(html: str) -> int:
    return len(WS.sub(" ", TAGS.sub(" ", html or "")).strip())


def extract_first_match(html: str, selectors: list[str], max_length: int) -> str:
    """The previous behaviour, verbatim, so both run on the same page.

    Copied rather than imported because the point of the script is to compare
    against the old behaviour after the function has changed. The fallback
    branch matters: an earlier version of this script omitted it and reported
    every "before" as 0 chars, which reads as an enormous win for the new code
    and says nothing at all.
    """
    from scripts.enrichers.ad_remover import remove_ads_and_boilerplate

    soup = BeautifulSoup(html, "html.parser")
    for selector in selectors or [
        "article",
        ".article-content",
        ".post-content",
        ".entry-content",
        ".content",
        "#content",
        ".main-content",
        ".blog-post-content",
        ".post-body",
        ".article-body",
        ".the-content",
        ".postText",
        ".txt",
        ".article-text",
        ".article",
        "[itemprop='articleBody']",
    ]:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        if not elements:
            continue
        content = elements[0]
        for sel in DEFAULT_AD_SELECTORS:
            for el in content.select(sel):
                el.decompose()
        result = _truncate_at_footer(content.decode_contents())
        return result[:max_length] if max_length > 0 else result
    result = _truncate_at_footer(remove_ads_and_boilerplate(html))
    return result[:max_length] if max_length > 0 else result


def article_feeds() -> list[Path]:
    import yaml

    from scripts.enrich_feeds import _resolve_mode

    sites = yaml.safe_load(Path("config/sites.yaml").read_text(encoding="utf-8"))["sites"]
    sites = sites.values() if isinstance(sites, dict) else sites
    out = []
    for site in sites:
        if not isinstance(site, dict):
            continue
        if _resolve_mode(site.get("category"), site.get("enhance_mode")) != "article":
            continue
        path = Path("feeds") / site["feed_file"]
        if path.is_file():
            out.append(path)
    return sorted(out)


async def run(paths: list[Path], limit: int) -> int:
    client = httpx.AsyncClient(follow_redirects=True, timeout=20)
    total_before = total_after = 0
    worse = better = 0
    try:
        for path in paths:
            root = ET.parse(path).getroot()
            channel = root.find("channel")
            if channel is None:
                continue
            links = []
            for item in channel.findall("item"):
                link = item.findtext("link")
                if link and link.startswith("http"):
                    links.append(link)
                if len(links) >= limit:
                    break
            b_sum = a_sum = 0
            b_n = a_n = 0
            for link in links:
                try:
                    html = await _fetch_article_page(link, client=client, timeout=20)
                except Exception:
                    continue
                if not html or looks_like_challenge(html):
                    continue
                b = text_len(extract_first_match(html, [], 50_000))
                a = text_len(extract_main_content(html, [], 50_000))
                b_sum += b
                a_sum += a
                # Both under the floor means neither is publishable; not a diff.
                if b < MIN_BODY_TEXT and a < MIN_BODY_TEXT:
                    continue
                b_n += 1
                a_n += 1
                if a < b:
                    worse += 1
                    print(f"      worse  {path.stem:28} {b:6} -> {a:6}  {link[:58]}")
                elif a > b:
                    better += 1
            if b_n or a_n:
                verdict = "same" if b_sum == a_sum else ("better" if a_sum > b_sum else "WORSE")
                print(
                    f"  {path.stem:30} {b_sum:8} -> {a_sum:8}  "
                    f"({a_sum - b_sum:+7})  {verdict}"
                )
                total_before += b_sum
                total_after += a_sum
    finally:
        await client.aclose()
    print(
        f"\n  total {total_before} -> {total_after} ({total_after - total_before:+d})"
        f"   items better={better} worse={worse}"
    )
    return 1 if worse else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--site", action="append", help="feed name, repeatable")
    ap.add_argument("--limit", type=int, default=4, help="items per feed")
    args = ap.parse_args(argv)
    paths = article_feeds()
    if args.site:
        paths = [p for p in paths if p.stem in args.site]
    if not paths:
        print("no article feeds found (run the pipeline first)")
        return 1
    return asyncio.run(run(paths, args.limit))


if __name__ == "__main__":
    sys.exit(main())
