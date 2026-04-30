from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, cast

import yaml


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
            )
        )

    return Config(sites=sites)


def _normalize_fetch_method(method: str) -> FetchMethod:
    normalized = str(method).strip().lower()
    if normalized == "httpx":
        return "http"
    if normalized in {"http", "cloudscraper", "playwright"}:
        return cast(FetchMethod, normalized)
    return "http"
