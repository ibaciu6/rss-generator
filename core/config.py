from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, cast

import yaml
import re

FetchMethod = Literal["http", "httpx", "cloudscraper", "playwright"]


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
    display_name: Optional[str] = None
    description_selector: Optional[str] = None
    date_selector: Optional[str] = None
    feed_file: str = "feed.xml"
    category: Optional[str] = None
    fallback_urls: List[str] = field(default_factory=list)
    blocked_content_markers: List[str] = field(default_factory=list)
    # If non-empty, HTML must contain every substring (case-insensitive) or fetch fails
    # and the next strategy (e.g. Playwright) is tried. Use when bots get 200 responses
    # without the real listing DOM.
    required_content_markers: List[str] = field(default_factory=list)
    # OR-of-ANDs: fetch passes if any inner group matches (every marker in that group
    # is present). When empty, `required_content_markers` is treated as a single group.
    required_content_marker_groups: Tuple[Tuple[str, ...], ...] = field(default_factory=tuple)
    blocked_final_hosts: List[str] = field(default_factory=list)
    allowed_final_hosts: List[str] = field(default_factory=list)
    allow_empty_title: bool = False
    # Optional post-processing transform applied to every extracted title string.
    # Supported values: "title_case" (converts ALL-CAPS site titles to Title Case).
    title_transform: Optional[str] = None
    detail_method: Optional[FetchMethod] = None
    detail_title_selector: Optional[str] = None
    detail_description_selector: Optional[str] = None
    max_items: Optional[int] = None
    # If set, Playwright waits for this CSS selector before reading the DOM (helps JS-filled listings).
    playwright_wait_selector: Optional[str] = None
    # If set, Playwright scrolls this selector into view step by step before reading DOM
    # (triggers lazy-load images in carousels). Value is a CSS selector for the scroll container,
    # or "window" to scroll the page.
    playwright_scroll_to: Optional[str] = None
    # Language tag for grouping feeds on the index page (e.g. "ro", "en").
    language: str = "ro"
    # Regex patterns for filtering items by title. Items whose title matches any
    # pattern are excluded from the feed. Applied case-insensitively.
    title_filter_patterns: List[str] = field(default_factory=list)
    # XPath selector to extract category tags from each item node (relative to item).
    # Used with blocked_categories to filter out unwanted sections (e.g. adult content).
    category_selector: Optional[str] = None
    # Category values to block. Items whose extracted category matches any entry
    # are filtered out. Only evaluated when category_selector is set.
    blocked_categories: List[str] = field(default_factory=list)
    # How many pages to scrape (WordPress /page/N/ pagination). Only useful when
    # filters (title_filter_patterns / blocked_categories) reduce the pool so much
    # that few items remain. Default 1 = no extra pages.
    pages: int = 1
    # Whether this feed is enabled. Disabled feeds are skipped during generation.
    # Use this to keep duplicate/fallback feeds in config without generating them.
    enabled: bool = True

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
            
        if not self.item_selector.strip():
            raise ValueError("Item selector cannot be empty")
            
        if not self.title_selector.strip():
            raise ValueError("Title selector cannot be empty")
            
        if not self.link_selector.strip():
            raise ValueError("Link selector cannot be empty")
            
        # Validate method
        if self.method not in {"http", "httpx", "cloudscraper", "playwright"}:
            raise ValueError(f"Invalid method: {self.method}. Must be one of: http, httpx, cloudscraper, playwright")
            
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
        if not re.match(r'^[a-z]{2}(-[A-Z]{2})?$', self.language):
            # Allow common language codes like "en", "ro", "en-US"
            if not re.match(r'^[a-z]{2}$', self.language.lower()):
                raise ValueError(f"Invalid language format: {self.language}. Expected format like 'en' or 'ro'")
                
        # Validate title_transform
        if self.title_transform is not None and self.title_transform not in {"title_case"}:
            raise ValueError(f"title_transform must be None or 'title_case', got: {self.title_transform}")


@dataclass(frozen=True)
class Config:
    """
    Root configuration model for all sites.
    """

    sites: List[SiteConfig]


def _parse_marker_groups(cfg: dict) -> Tuple[Tuple[str, ...], ...]:
    """
    Build marker OR-groups from YAML.

    ``required_content_marker_groups: [["a","b"], ["c"]]`` → pass if (a AND b) OR (c).
    If absent, fall back to a single group from ``required_content_markers`` (AND).
    """
    raw_groups = cfg.get("required_content_marker_groups")
    if raw_groups:
        out: List[Tuple[str, ...]] = []
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

    raw_sites: Dict[str, Dict] = data.get("sites", {})
    sites: List[SiteConfig] = []

    for name, cfg in raw_sites.items():
        sites.append(
            SiteConfig(
                name=name,
                url=str(cfg["url"]),
                method=_normalize_fetch_method(cfg.get("method", "http")),
                item_selector=str(cfg["item_selector"]),
                title_selector=str(cfg["title_selector"]),
                link_selector=str(cfg["link_selector"]),
                display_name=cfg.get("display_name"),
                description_selector=cfg.get("description_selector"),
                date_selector=cfg.get("date_selector"),
                feed_file=str(cfg.get("feed_file", f"{name}.xml")),
                category=cfg.get("category"),
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
            )
        )

    return Config(sites=sites)


def _normalize_fetch_method(method: str) -> FetchMethod:
    normalized = str(method).strip().lower()
    if normalized == "httpx":
        return "http"
    if normalized in {"http", "cloudscraper", "playwright"}:
        return cast(FetchMethod, normalized)
    # Default to http for unknown methods
    return "http"
