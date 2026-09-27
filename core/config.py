from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, cast

import yaml

FetchMethod = Literal["http", "httpx", "cloudscraper", "playwright", "rss"]


@dataclass(frozen=True)
class SiteConfig:
    """
    Configuration for a single site feed.
    """

    name: str
    url: str
    method: FetchMethod
    item_selector: str
    title_selector: str
    link_selector: str
    display_name: str | None = None
    description_selector: str | None = None
    date_selector: str | None = None
    feed_file: str = "feed.xml"
    category: str | None = None
    # Content type of the feed: "movie" or "series". Drives TV-only enrichments
    # such as the EpGuides link. When unset, a per-item title heuristic is used.
    kind: str | None = None
    fallback_urls: list[str] = field(default_factory=list)
    blocked_content_markers: list[str] = field(default_factory=list)
    # If non-empty, HTML must contain every substring (case-insensitive) or fetch fails
    # and the next strategy (e.g. Playwright) is tried. Use when bots get 200 responses
    # without the real listing DOM.
    required_content_markers: list[str] = field(default_factory=list)
    # OR-of-ANDs: fetch passes if any inner group matches (every marker in that group
    # is present). When empty, `required_content_markers` is treated as a single group.
    required_content_marker_groups: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    blocked_final_hosts: list[str] = field(default_factory=list)
    allowed_final_hosts: list[str] = field(default_factory=list)
    allow_empty_title: bool = False
    # Optional post-processing transform applied to every extracted title string.
    # Supported values: "title_case" (converts ALL-CAPS site titles to Title Case).
    title_transform: str | None = None
    detail_method: FetchMethod | None = None
    detail_title_selector: str | None = None
    detail_description_selector: str | None = None
    max_items: int | None = None
    # If set, Playwright waits for this CSS selector before reading the DOM (helps JS-filled listings).
    playwright_wait_selector: str | None = None
    # If set, Playwright scrolls this selector into view step by step before reading DOM
    # (triggers lazy-load images in carousels). Value is a CSS selector for the scroll container,
    # or "window" to scroll the page.
    playwright_scroll_to: str | None = None
    # Language tag for grouping feeds on the index page (e.g. "ro", "en").
    language: str = "ro"
    # Regex patterns for filtering items by title. Items whose title matches any
    # pattern are excluded from the feed. Applied case-insensitively.
    title_filter_patterns: list[str] = field(default_factory=list)
    # XPath selector to extract category tags from each item node (relative to item).
    # Used with blocked_categories to filter out unwanted sections (e.g. adult content).
    category_selector: str | None = None
    # Category values to block. Items whose extracted category matches any entry
    # are filtered out. Only evaluated when category_selector is set.
    blocked_categories: list[str] = field(default_factory=list)
    # How many pages to scrape (WordPress /page/N/ pagination). Only useful when
    # filters (title_filter_patterns / blocked_categories) reduce the pool so much
    # that few items remain. Default 1 = no extra pages.
    pages: int = 1
    # Whether this feed is enabled. Disabled feeds are skipped during generation.
    # Use this to keep duplicate/fallback feeds in config without generating them.
    enabled: bool = True

    # Per-site override of the enrichment mode chosen from the site's category
    # by scripts/enrich_feeds.py. None means "use the category default".
    # - "streaming": TMDb poster lookup, IMDb links, trailer links, EpGuides
    # - "article": Full article content extraction with ad removal
    # - "none": No additional enrichment
    enhance_mode: str | None = None
    # XPaths for extracting article content from detail pages (for non-RSS feeds)
    detail_article_selector: str | None = None
    # CSS selectors for removing ads and boilerplate (for article enrichment)
    ad_selectors: list[str] = field(default_factory=lambda: [
        ".ad", ".ad-container", ".advertisement", "#sidebar", ".sidebar",
        ".social-share", ".comments", ".related-posts"
    ])
    # Named removal modules to apply to the article body, in order. Each module
    # is implemented once in scripts/enrichers/removal_modules.py and shared
    # across every feed that lists it. When empty, only ad_selectors runs.
    removals: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Validate required fields are not empty
        if not self.name.strip():
            raise ValueError("Site name cannot be empty")
        
        if not self.url.strip():
            raise ValueError("Site URL cannot be empty")
            
        # Basic URL format validation
        if not (self.url.startswith("http://") or self.url.startswith("https://")):
            raise ValueError(f"Invalid URL format: {self.url}. Must start with http:// or https://")
            
        # Native RSS/Atom feeds carry their own structure; XPath selectors don't apply.
        if self.method != "rss":
            if not self.item_selector.strip():
                raise ValueError("Item selector cannot be empty")

            if not self.title_selector.strip():
                raise ValueError("Title selector cannot be empty")

            if not self.link_selector.strip():
                raise ValueError("Link selector cannot be empty")

        feed_path = Path(self.feed_file)
        if (
            not self.feed_file.strip()
            or feed_path.name != self.feed_file
            or feed_path.suffix != ".xml"
        ):
            raise ValueError(
                "feed_file must be a simple .xml filename, for example 'example.xml'"
            )
            
        # Validate method
        if self.method not in {"http", "httpx", "cloudscraper", "playwright", "rss"}:
            raise ValueError(f"Invalid method: {self.method}. Must be one of: http, httpx, cloudscraper, playwright, rss")
            
        # Validate the normalized method for httpx -> http conversion
        if self.method == "httpx":
            # This should be normalized to "http" by _normalize_fetch_method
            pass  # The normalization happens in load_config
            
        # Validate numeric fields
        if self.max_items is not None and self.max_items <= 0:
            raise ValueError(f"max_items must be positive, got: {self.max_items}")
            
        if self.pages < 1:
            raise ValueError(f"pages must be at least 1, got: {self.pages}")
            
        # Validate language format (basic check)
        # Allow common language codes like "en", "ro", "en-US"
        if not re.match(r'^[a-z]{2}(-[A-Z]{2})?$', self.language) and not re.match(
            r'^[a-z]{2}$', self.language.lower()
        ):
            raise ValueError(f"Invalid language format: {self.language}. Expected format like 'en' or 'ro'")

        if self.kind is not None and self.kind not in {"movie", "series"}:
            raise ValueError(f"kind must be None, 'movie', or 'series', got: {self.kind}")

        if self.enhance_mode is not None and self.enhance_mode not in {"streaming", "article", "none"}:
            raise ValueError(
                f"enhance_mode must be None, 'streaming', 'article', or 'none', got: {self.enhance_mode}"
            )
                
        # Validate title_transform
        if self.title_transform is not None and self.title_transform not in {"title_case"}:
            raise ValueError(f"title_transform must be None or 'title_case', got: {self.title_transform}")

        # Validate removal module names. Imported lazily so core never depends
        # on the enrichers package at module load.
        if self.removals:
            from scripts.enrichers.removal_modules import known_modules

            unknown = set(self.removals) - set(known_modules())
            if unknown:
                raise ValueError(
                    f"{self.name}: unknown removal module(s) {sorted(unknown)}; "
                    f"known modules: {known_modules()}"
                )


@dataclass(frozen=True)
class Config:
    """
    Root configuration model for all sites.
    """

    sites: list[SiteConfig]


def _parse_marker_groups(cfg: dict) -> tuple[tuple[str, ...], ...]:
    """
    Build marker OR-groups from YAML.

    ``required_content_marker_groups: [["a","b"], ["c"]]`` → pass if (a AND b) OR (c).
    If absent, fall back to a single group from ``required_content_markers`` (AND).
    """
    raw_groups = cfg.get("required_content_marker_groups")
    if raw_groups:
        out: list[tuple[str, ...]] = []
        for group in raw_groups:
            if not isinstance(group, (list, tuple)):
                continue
            cleaned = tuple(str(x).strip() for x in group if str(x).strip())
            if cleaned:
                out.append(cleaned)
        return tuple(out)
    legacy = [str(m).strip() for m in (cfg.get("required_content_markers") or []) if str(m).strip()]
    if legacy:
        return (tuple(legacy),)
    return ()


def load_config(path: Path) -> Config:
    """
    Load configuration from a YAML file.
    """
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_sites: dict[str, dict] = data.get("sites", {})
    if not isinstance(raw_sites, dict):
        raise ValueError("'sites' must be a mapping of site names to configurations")

    accepted_keys = {field.name for field in fields(SiteConfig)} | {
        "required_content_markers"
    }
    sites: list[SiteConfig] = []
    feed_files: set[str] = set()

    for name, cfg in raw_sites.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Configuration for site {name!r} must be a mapping")
        unknown_keys = set(cfg) - accepted_keys
        if unknown_keys:
            unknown = ", ".join(sorted(map(str, unknown_keys)))
            raise ValueError(f"Unknown configuration key(s) for site {name!r}: {unknown}")
        site = SiteConfig(
                name=name,
                url=str(cfg["url"]),
                method=_normalize_fetch_method(cfg.get("method", "http")),
                item_selector=str(cfg.get("item_selector", "")),
                title_selector=str(cfg.get("title_selector", "")),
                link_selector=str(cfg.get("link_selector", "")),
                display_name=cfg.get("display_name"),
                description_selector=cfg.get("description_selector"),
                date_selector=cfg.get("date_selector"),
                feed_file=str(cfg.get("feed_file", f"{name}.xml")),
                category=cfg.get("category"),
                kind=cfg.get("kind"),
                fallback_urls=[str(url) for url in cfg.get("fallback_urls", [])],
                blocked_content_markers=[
                    str(marker) for marker in cfg.get("blocked_content_markers", [])
                ],
                required_content_markers=[
                    str(marker) for marker in cfg.get("required_content_markers", [])
                ],
                required_content_marker_groups=_parse_marker_groups(cfg),
                blocked_final_hosts=[str(host) for host in cfg.get("blocked_final_hosts", [])],
                allowed_final_hosts=[str(host) for host in cfg.get("allowed_final_hosts", [])],
                allow_empty_title=bool(cfg.get("allow_empty_title", False)),
                title_transform=cfg.get("title_transform"),
                detail_method=cfg.get("detail_method"),
                detail_title_selector=cfg.get("detail_title_selector"),
                detail_description_selector=cfg.get("detail_description_selector"),
                max_items=cfg.get("max_items"),
                playwright_wait_selector=cfg.get("playwright_wait_selector"),
                playwright_scroll_to=cfg.get("playwright_scroll_to"),
                language=str(cfg.get("language", "ro")),
                title_filter_patterns=[
                    str(p) for p in cfg.get("title_filter_patterns", [])
                ],
                category_selector=cfg.get("category_selector"),
                blocked_categories=[
                    str(c) for c in cfg.get("blocked_categories", [])
                ],
                pages=int(cfg.get("pages", 1)),
                enabled=bool(cfg.get("enabled", True)),
                enhance_mode=cfg.get("enhance_mode"),
                detail_article_selector=cfg.get("detail_article_selector"),
                ad_selectors=[str(s) for s in cfg.get("ad_selectors", [
                    ".ad", ".ad-container", ".advertisement", "#sidebar", ".sidebar",
                    ".social-share", ".comments", ".related-posts"
                ])],
                removals=[str(s) for s in cfg.get("removals", [])],
            )
        if site.feed_file in feed_files:
            raise ValueError(f"Duplicate feed_file in configuration: {site.feed_file}")
        feed_files.add(site.feed_file)
        sites.append(site)

    return Config(sites=sites)


def resolve_feed_files(config: Config, wanted: Sequence[str] | None) -> tuple[set[str], list[str]]:
    """Resolve site names / feed filenames to the matching ``feed_file`` values.

    A request matches a site when its ``name`` equals the request, or when its
    ``feed_file`` equals the request with or without the ``.xml`` suffix, so
    ``showrss``, ``showrss.xml`` and a custom ``feed_file`` all work. This is
    the same matching rule :meth:`GenerationEngine._select_sites` uses, shared
    so the post-generation stages (enrich, fix) accept identical input.

    Args:
        config: The loaded configuration.
        wanted: Requested names. Empty or ``None`` means "every site".

    Returns:
        ``(feed_files, unmatched)`` -- the matched ``feed_file`` values, and the
        requests that matched nothing. ``unmatched`` is what lets a caller tell
        "disabled site" apart from "typo" instead of silently doing nothing.
    """
    requests = {s.strip() for s in (wanted or []) if s and s.strip()}
    if not requests:
        return {site.feed_file for site in config.sites}, []

    # Accept the feed_file with or without its .xml suffix.
    names = requests | {r.removesuffix(".xml") for r in requests}
    matched = {
        site.feed_file
        for site in config.sites
        if site.name in names or Path(site.feed_file).stem in names
    }

    def _hit(request: str) -> bool:
        stem = request.removesuffix(".xml")
        return any(
            site.name == request
            or site.name == stem
            or Path(site.feed_file).stem == stem
            for site in config.sites
        )

    # Report only what the user actually typed, so `--site showrss.xml` is not
    # flagged just because .xml was stripped for matching.
    return matched, sorted(r for r in requests if not _hit(r))


def _normalize_fetch_method(method: str) -> FetchMethod:
    normalized = str(method).strip().lower()
    if normalized == "httpx":
        return "http"
    if normalized in {"http", "cloudscraper", "playwright", "rss"}:
        return cast(FetchMethod, normalized)
    raise ValueError(
        f"Invalid method: {method}. Must be one of: http, httpx, cloudscraper, playwright, rss"
    )
