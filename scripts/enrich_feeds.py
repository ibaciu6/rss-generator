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

import httpx

from core.config import load_config
from core.logging_utils import get_logger
from scripts.enrichers.article_enricher import ArticleEnrichConfig, enrich_article_feed
from scripts.enrichers.streaming_enricher import process_feed as process_streaming_feed

FEEDS_DIR = Path(__file__).resolve().parent.parent / "feeds"
REPO_ROOT = Path(__file__).resolve().parent.parent

logger = get_logger(__name__)


def _get_category_enrichment_config(category: str) -> dict:
    """Get enrichment configuration for a feed category."""
    configs = {
        "movies": {
            "mode": "streaming",
            "add_posters": True,
            "add_imdb_links": True,
            "add_trailer_links": True,
        },
        "episodes": {
            "mode": "streaming",
            "add_epguides": True,
            "add_posters": True,
            "add_imdb_links": True,
            "add_trailer_links": True,
        },
        "series": {
            "mode": "streaming",
            "add_epguides": True,
            "add_posters": True,
            "add_imdb_links": True,
            "add_trailer_links": True,
        },
        "blogs": {
            "mode": "article",
            "add_featured_image": True,
            "fetch_full_content": True,
            "remove_ads": True,
        },
        "news": {
            "mode": "article",
            "add_featured_image": True,
            "fetch_full_content": True,
            "remove_ads": True,
        },
        "cyber": {
            "mode": "article",
            "add_featured_image": True,
            "fetch_full_content": True,
            "remove_ads": True,
        },
        "tech": {
            "mode": "article",
            "add_featured_image": True,
            "fetch_full_content": True,
            "remove_ads": True,
        },
        "education": {
            "mode": "article",
            "add_featured_image": True,
            "fetch_full_content": True,
            "remove_ads": True,
        },
        "economy": {
            "mode": "article",
            "add_featured_image": True,
            "fetch_full_content": True,
            "remove_ads": True,
        },
        "local": {
            "mode": "article",
            "add_featured_image": True,
            "fetch_full_content": True,
            "remove_ads": True,
        },
        "cinema": {
            "mode": "catalog",
            "add_posters": True,
            "add_showtimes": True,
        },
        "torrents": {
            "mode": "torrent",
            "add_metadata": True,
        },
    }
    return configs.get(category, {"mode": "none"})


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
        print("SKIP  TMDB_API_KEY not set — skipping enrichment")

    # Load site configuration to get categories
    config_path = REPO_ROOT / "config" / "sites.yaml"
    site_configs = load_config(config_path)

    # Build a map of feed file → site config
    feed_config: dict[str, any] = {}
    for site in site_configs.sites:
        feed_config[site.feed_file] = {
            "category": site.category,
            "url": site.url,
            "base_url": site.url.split("://")[1].split("/")[0] if "://" in site.url else site.url,
        }

    # Get all feed files
    xml_files = sorted(FEEDS_DIR.glob("*.xml"))
    total_feeds = len(xml_files)

    # Statistics
    total_items = 0
    total_posters = 0
    total_years = 0
    total_links = 0
    total_epguides = 0
    total_enriched = 0
    total_errors = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        for idx, path in enumerate(xml_files, 1):
            feed_name = path.stem
            site_cfg = feed_config.get(path.name, {})
            category = site_cfg.get("category", "unknown")

            print(f"  [{idx}/{total_feeds}] {feed_name} ({category})... ", end="", flush=True)

            enrich_cfg = _get_category_enrichment_config(category)

            try:
                if enrich_cfg["mode"] == "streaming":
                    # Streaming feed: use TMDB enrichment
                    is_series_feed = category in ("episodes", "series")
                    changed, stats = process_streaming_feed(
                        path,
                        is_series_feed=is_series_feed,
                    )
                    total_items += stats.get("items", 0)
                    total_posters += stats.get("posters", 0)
                    total_years += stats.get("years", 0)
                    total_links += stats.get("links", 0)
                    total_epguides += stats.get("epguides", 0)

                elif enrich_cfg["mode"] == "article":
                    # Article/blog/news feed: fetch full content
                    article_cfg = ArticleEnrichConfig(
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
                    if enrich_cfg.get("add_featured_image"):
                        total_posters += stats.get("enriched", 0)

                elif enrich_cfg["mode"] == "torrent":
                    # Torrent feed: metadata enhancement (placeholder for now)
                    changed, stats = process_streaming_feed(path, is_series_feed=False)
                    total_items += stats.get("items", 0)

                else:
                    # No enrichment
                    changed, stats = False, {"items": 0}

                if changed:
                    total_enriched += 1

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

                total_items += stats.get("items", 0)

            except Exception as e:
                total_errors += 1
                parts = f"ERROR: {str(e)[:50]}"
                print(f"[ERR] {parts}")
                continue

            print(f"[{status}] {parts}")

    summary = f"Enriched {total_enriched}/{total_feeds} feeds | {total_items} items"
    if total_posters:
        summary += f" | {total_posters} posters"
    if total_years:
        summary += f" | {total_years} years added"
    if total_links:
        summary += f" | {total_links} links added"
    if total_epguides:
        summary += f" | {total_epguides} epguides"
    if total_errors:
        summary += f" | {total_errors} errors"

    print(summary)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())