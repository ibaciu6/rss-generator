#!/usr/bin/env python3
"""Enrich feed items with posters, years, IMDb links, full article content, and more.

This script orchestrates category-specific enrichment for different feed types:
- Streaming sites (movies, episodes, series): TMDb posters, IMDb links, trailer links
- Blogs and news sites: Full article content with ad removal
- Torrent sites: Metadata enhancement
- Cyber/News: Content extraction and media preservation
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from core.config import load_config
from core.logging_utils import get_logger
from scripts.enrichers.article_enricher import ArticleEnrichConfig, enrich_article_feed
from scripts.enrichers.streaming_enricher import _feed_kinds, resolve_epguides_misses
from scripts.enrichers.streaming_enricher import process_feed as process_streaming_feed

FEEDS_DIR = Path(__file__).resolve().parent.parent / "feeds"
REPO_ROOT = Path(__file__).resolve().parent.parent

logger = get_logger(__name__)

# Enrichment modes, dispatched per feed in ``main()``:
#   streaming - TMDb posters/years + IMDb/trailer links + EpGuides (movies, episodes, …)
#   article   - fetch the full article body, strip ads, keep a featured image
#   none      - leave the feed untouched
#
# Every category used by config/sites.yaml must appear here. An unknown category
# falls back to STREAMING so a newly added site keeps the enrichment it had
# before per-category routing existed, rather than silently losing it.
DEFAULT_MODE = "streaming"

_STREAMING_CFG = {
    "mode": "streaming",
    "add_posters": True,
    "add_imdb_links": True,
    "add_trailer_links": True,
    "add_epguides": True,
}
_ARTICLE_CFG = {
    "mode": "article",
    "add_featured_image": True,
    "replace_summary": True,
}

CATEGORY_ENRICHMENT: dict[str, dict] = {
    # --- streaming / TMDb ---
    "movies": _STREAMING_CFG,
    "episodes": _STREAMING_CFG,
    "cinema": _STREAMING_CFG,  # showtimes feeds also carry film titles worth a poster
    "torrents": _STREAMING_CFG,  # release titles resolve via TMDb search
    "releases": _STREAMING_CFG,  # r/SceneReleases - torrent release announcements
    # --- full-article body ---
    "blogs": _ARTICLE_CFG,
    "news": _ARTICLE_CFG,
    "cyber": _ARTICLE_CFG,
    "tech": _ARTICLE_CFG,
    "education": _ARTICLE_CFG,
    "economy": _ARTICLE_CFG,
    "local": _ARTICLE_CFG,
}


def _get_category_enrichment_config(category: str) -> dict:
    """Get enrichment configuration for a feed category."""
    return CATEGORY_ENRICHMENT.get(category, _STREAMING_CFG)


def _base_url(url: str) -> str:
    """Return the scheme-less host of a site URL (for resolving relative links)."""
    if not url:
        return ""
    parts = urlsplit(url)
    return parts.netloc or parts.path.split("/")[0]


def _resolve_mode(category: str | None, override: str | None) -> str:
    """Pick the enrichment mode for a feed.

    A per-site ``enhance_mode`` in config/sites.yaml wins over the category
    default, so an individual site can opt in or out of enrichment.
    """
    if override:
        return override
    return _get_category_enrichment_config(category or "")["mode"]


async def _enrich_with_article_content(
    path: Path,
    base_url: str,
    config: ArticleEnrichConfig,
    client: httpx.AsyncClient,
) -> tuple[bool, dict]:
    """Enrich an article/blog/news feed with full content."""
    return await enrich_article_feed(
        path,
        client=client,
        config=config,
        base_url=base_url,
    )


async def main():
    """Main entry point for feed enrichment."""
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        # Article enrichment does not need TMDb, so only the streaming half is
        # skipped here. Falling through to the streaming branch without a key
        # would burn an API call per item and fail every one of them.
        print("WARN  TMDB_API_KEY not set - streaming enrichment disabled (article enrichment still runs)")

    # Load site configuration to get categories
    config_path = REPO_ROOT / "config" / "sites.yaml"
    site_configs = load_config(config_path)

    # Build a map of feed file → site config
    feed_config: dict[str, dict] = {}
    for site in site_configs.sites:
        feed_config[site.feed_file] = {
            "category": site.category,
            "url": site.url,
            "base_url": _base_url(site.url),
            "enhance_mode": getattr(site, "enhance_mode", None),
            "detail_article_selector": getattr(site, "detail_article_selector", None),
            "ad_selectors": list(getattr(site, "ad_selectors", []) or []),
        }

    # Get all feed files
    xml_files = sorted(FEEDS_DIR.glob("*.xml"))
    total_feeds = len(xml_files)

    # Series-ness comes from the site config's `kind` field, not the category:
    # e.g. showrss.xml is `category: movies` but `kind: series`. Passing None
    # for undeclared sites lets process_feed fall back to its per-item SxxEyy
    # heuristic, which is what the pre-split enrich_posters.py did.
    feed_kinds = _feed_kinds()
    epguides_misses: dict[Path, list] = {}

    # Statistics
    total_items = 0
    total_posters = 0
    total_years = 0
    total_links = 0
    total_epguides = 0
    total_enriched = 0
    total_errors = 0
    total_articles = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        for idx, path in enumerate(xml_files, 1):
            feed_name = path.stem
            site_cfg = feed_config.get(path.name, {})
            category = site_cfg.get("category") or "uncategorised"

            print(f"  [{idx}/{total_feeds}] {feed_name} ({category})... ", end="", flush=True)

            mode = _resolve_mode(site_cfg.get("category"), site_cfg.get("enhance_mode"))
            enrich_cfg = _get_category_enrichment_config(site_cfg.get("category") or "")

            try:
                if mode == "streaming":
                    if not api_key:
                        changed, stats = False, {"items": 0}
                    else:
                        kind = feed_kinds.get(path.name)
                        changed, stats = process_streaming_feed(
                            path,
                            is_series_feed=None if kind is None else kind == "series",
                            epguides_misses=epguides_misses,
                        )
                        total_posters += stats.get("posters", 0)
                        total_years += stats.get("years", 0)
                        total_links += stats.get("links", 0)
                        total_epguides += stats.get("epguides", 0)

                elif mode == "article":
                    # Article/blog/news feed: fetch full content. Per-site
                    # detail_article_selector / ad_selectors refine extraction.
                    content_selectors = []
                    if site_cfg.get("detail_article_selector"):
                        content_selectors.append(site_cfg["detail_article_selector"])
                    article_cfg = ArticleEnrichConfig(
                        content_selectors=content_selectors,
                        ad_selectors=site_cfg.get("ad_selectors", []),
                        add_featured_image=enrich_cfg.get("add_featured_image", True),
                        replace_summary=enrich_cfg.get("replace_summary", True),
                    )
                    changed, stats = await _enrich_with_article_content(
                        path,
                        base_url=site_cfg.get("base_url", ""),
                        config=article_cfg,
                        client=client,
                    )
                    total_items += stats.get("items", 0)
                    total_articles += stats.get("enriched", 0)

                else:
                    # mode == "none": leave the feed untouched
                    changed, stats = False, {"items": 0}

                if changed:
                    total_enriched += 1

                # Counted once per feed, after the branch, so no mode can
                # double-count its items into the run total.
                total_items += stats.get("items", 0)

                status = "OK" if changed else "no-change"
                parts = f"items={stats.get('items', 0)}"
                if stats.get("posters"):
                    parts += f" posters={stats['posters']}"
                if stats.get("years"):
                    parts += f" years={stats['years']}"
                if stats.get("links"):
                    parts += f" links={stats['links']}"
                if stats.get("epguides"):
                    parts += f" epguides={stats['epguides']}"
                if stats.get("enriched"):
                    parts += f" rich={stats['enriched']}"
                if stats.get("skipped"):
                    parts += f" skipped={stats['skipped']}"
                if stats.get("errors"):
                    parts += f" errors={stats['errors']}"

            except Exception as e:
                total_errors += 1
                print(f"[ERR] {type(e).__name__}: {str(e)[:80]}")
                continue

            print(f"[{status}] {parts}")

    # Second pass: TV series missing from the local EpGuides mirror get a fresh
    # allshows.txt and one more attempt (exact page, else a site-search link).
    if epguides_misses:
        total_epguides += resolve_epguides_misses(epguides_misses, feed_kinds)

    summary = f"Enriched {total_enriched}/{total_feeds} feeds | {total_items} items"
    if total_posters:
        summary += f" | {total_posters} posters"
    if total_years:
        summary += f" | {total_years} years added"
    if total_links:
        summary += f" | {total_links} links added"
    if total_epguides:
        summary += f" | {total_epguides} epguides"
    if total_articles:
        summary += f" | {total_articles} article bodies"
    if total_errors:
        summary += f" | {total_errors} errors"

    print(summary)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())