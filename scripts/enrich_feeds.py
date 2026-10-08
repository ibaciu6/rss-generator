#!/usr/bin/env python3
"""Enrich feed items with posters, years, IMDb links, full article content, and more.

This script orchestrates category-specific enrichment for different feed types:
- Streaming sites (movies, episodes, series): TMDb posters, IMDb links, trailer links
- Blogs and news sites: Full article content with ad removal
- Torrent sites: Metadata enhancement
- Cyber/News: Content extraction and media preservation
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from core.config import load_config, resolve_feed_files
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


def _proxy_url() -> str:
    """The configured article-fetch proxy, or "" when there is none.

    The generate step has read `RSS_GENERATOR_PROXY_URL` since the fetcher was
    written; article enrichment did not, so setting the secret only fixed half
    the pipeline. The symptom was visible in a production run: hackread serves
    its feed over the bare host with a 200 and 403s its article pages from a
    datacenter address, so the feed generated and every item came back as the
    site's own excerpt, with nothing saying the address was the problem.
    """
    return (os.environ.get("RSS_GENERATOR_PROXY_URL") or "").strip()


def _client_kwargs() -> dict:
    """Kwargs for the shared httpx client used by every article fetch.

    The per-request fallback in `article_enricher` is not the path that
    matters: the enrich loop shares one client, so a proxy applied only to the
    one-shot call would reach nothing.
    """
    proxy = _proxy_url()
    return {"timeout": 15.0, **({"proxy": proxy} if proxy else {})}


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


async def main(argv: list[str] | None = None) -> int:
    """Main entry point for feed enrichment.

    Args:
        argv: CLI arguments. ``--site NAME`` restricts the run to those feeds
            (repeatable, accepts a site name or a ``.xml`` filename). Omit it to
            enrich every feed in ``feeds/``.

    Returns:
        Process exit code: 0 on success, 1 if a requested site matched nothing.
    """
    parser = argparse.ArgumentParser(
        prog="enrich_feeds",
        description="Enrich feed items with posters, years, IMDb links and full article content.",
    )
    parser.add_argument(
        "--site",
        dest="sites",
        action="append",
        metavar="NAME",
        help=(
            "Only enrich this feed (matches the site `name` or the feed_file, with or "
            "without .xml). Repeatable. Defaults to every feed in feeds/."
        ),
    )
    # argv or [] rather than argv: a programmatic main() call must never pick up
    # the *process* arguments, which is what argparse's None default would do.
    args = parser.parse_args(argv or [])

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
            "removals": list(getattr(site, "removals", []) or []),
            "keep_titles": bool(getattr(site, "keep_titles", False)),
        }

    # Restrict to the requested sites, if any. Done before globbing so a typo
    # fails loudly instead of quietly re-enriching all 70 feeds.
    wanted, unmatched = resolve_feed_files(site_configs, args.sites)
    for name in unmatched:
        print(f"ERROR unknown site: {name}", file=sys.stderr)
    if args.sites and not wanted:
        return 1

    xml_files = [p for p in sorted(FEEDS_DIR.glob("*.xml")) if not args.sites or p.name in wanted]
    if args.sites:
        # A configured site whose feed file was never generated is a real
        # problem for a targeted run, unlike in a full sweep.
        missing = sorted(wanted - {p.name for p in xml_files})
        for feed_file in missing:
            print(f"ERROR no such feed file: {FEEDS_DIR / feed_file} (run generate for it first)", file=sys.stderr)
        if missing:
            return 1
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
    total_kept_excerpt = 0
    total_fetch_failed = 0
    # Feeds where *every* article fetch failed. A partial failure is a site
    # being flaky; a total one is a pattern, and the useful question is which.
    blocked_hosts: list[str] = []
    proxy = _proxy_url()

    if _proxy_url():
        print("  (article fetches via the configured proxy)")
    async with httpx.AsyncClient(**_client_kwargs()) as client:
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
                            feed_category=category,
                            keep_titles=bool(site_cfg.get("keep_titles", False)),
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
                        removals=site_cfg.get("removals", []),
                    )
                    changed, stats = await _enrich_with_article_content(
                        path,
                        base_url=site_cfg.get("base_url", ""),
                        config=article_cfg,
                        client=client,
                    )
                    total_articles += stats.get("enriched", 0)
                    total_kept_excerpt += stats.get("kept_excerpt", 0)
                    total_fetch_failed += stats.get("fetch_failed", 0)
                    if (
                        stats.get("fetch_failed")
                        and stats.get("fetch_failed", 0) >= stats.get("items", 0)
                    ):
                        blocked_hosts.append(feed_name)

                else:
                    # mode == "none": leave the feed untouched
                    changed, stats = False, {"items": 0}

                if changed:
                    total_enriched += 1

                # Counted once per feed, after the branch, so no mode can
                # double-count its items into the run total. Branches therefore
                # add their own specific counters only (posters, articles, ...).
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
                # Spelled out because "skipped" reads like a bookkeeping detail
                # and is not one: every one of these items is a stub in the
                # published feed, indistinguishable from an article.
                if stats.get("kept_excerpt"):
                    parts += f" ON-EXCERPT={stats['kept_excerpt']}"
                    if stats.get("fetch_failed"):
                        parts += f"(fetch {stats['fetch_failed']}"
                    if stats.get("challenge"):
                        parts += ("," if stats.get("fetch_failed") else "(") + f"wall {stats['challenge']}"
                    if stats.get("chrome_only"):
                        parts += ("," if (stats.get("fetch_failed") or stats.get("challenge")) else "(")
                        parts += f"chrome {stats['chrome_only']}"
                    if stats.get("fetch_failed") or stats.get("challenge") or stats.get("chrome_only"):
                        parts += ")"
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
    if total_kept_excerpt:
        # Deliberately not a footnote. A feed whose items all keep the site's
        # own snippet looks identical to an enriched one everywhere downstream,
        # and this is the only place the difference is recorded.
        summary += f" | !! {total_kept_excerpt} ITEMS STILL ON THEIR SITE EXCERPT"
        # ...and the interesting half of that number is *why*. A fetch failure
        # and a site that has gone quiet produce the same published item, and
        # 108 items once vanished into per-feed "fetch 19" markers with nothing
        # in the log to say the address was at fault. Measured directly:
        # ghacks, doublepulsar and hackread answer 403 to GitHub's runners and
        # serve normally from a residential address; edu.ro times out
        # connecting. None of that is a code fault, and all of it is invisible
        # unless it is said.
        if total_fetch_failed:
            if proxy:
                summary += (
                    f" ({total_fetch_failed} could not be fetched even through"
                    f" the configured proxy)"
                )
            else:
                summary += (
                    f" ({total_fetch_failed} could not be fetched from this"
                    f" network; RSS_GENERATOR_PROXY_URL is not set, so this runs"
                    f" from the CI address)"
                )
                if blocked_hosts:
                    summary += f" | fetched by none, in: {', '.join(blocked_hosts)}"
    if total_errors:
        summary += f" | {total_errors} errors"

    print(summary)
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main(sys.argv[1:])))