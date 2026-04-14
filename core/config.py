from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, cast

import yaml


FetchMethod = Literal["http", "httpx", "cloudscraper", "playwright"]
ConfigBucket = Literal["movies", "series"]


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
    blocked_final_hosts: List[str] = field(default_factory=list)
    allowed_final_hosts: List[str] = field(default_factory=list)
    allow_empty_title: bool = False
    detail_method: Optional[FetchMethod] = None
    detail_title_selector: Optional[str] = None
    detail_description_selector: Optional[str] = None
    max_items: Optional[int] = None
    # If set, Playwright waits for this CSS selector before reading the DOM (helps JS-filled listings).
    playwright_wait_selector: Optional[str] = None
    # Optional GitHub Actions schedule cron for this site (informational; workflows embed it).
    schedule_cron: Optional[str] = None
    # Set when the site file lives under ``config/sites/movies/`` or ``config/sites/series/``.
    config_bucket: Optional[ConfigBucket] = None


@dataclass(frozen=True)
class Config:
    """
    Root configuration model for all sites.
    """

    sites: List[SiteConfig]


def _flat_site_yamls(config_dir: Path) -> List[Path]:
    return sorted(
        p for p in config_dir.glob("*.yaml") if not p.name.startswith("_")
    )


def uses_partitioned_site_layout(config_dir: Path) -> bool:
    """
    True when site YAML lives under ``movies/`` and ``series/`` (or the tree is empty
    and new files should use that layout). False for legacy flat ``config/sites/*.yaml``.
    """
    if (config_dir / "movies").is_dir() or (config_dir / "series").is_dir():
        return True
    return not _flat_site_yamls(config_dir)


def discover_site_yaml_files(
    config_dir: Path,
) -> List[Tuple[Path, Optional[ConfigBucket]]]:
    """
    Return ``(path, bucket)`` for each site file. ``bucket`` is ``None`` in the legacy
    flat layout (YAML files directly under ``config_dir``).
    """
    if (config_dir / "movies").is_dir() or (config_dir / "series").is_dir():
        out: List[Tuple[Path, ConfigBucket]] = []
        for bucket, sub in (
            ("movies", config_dir / "movies"),
            ("series", config_dir / "series"),
        ):
            if not sub.is_dir():
                continue
            for child in sorted(sub.glob("*.yaml")):
                if child.name.startswith("_"):
                    continue
                out.append((child, bucket))
        return out
    return [(p, None) for p in _flat_site_yamls(config_dir)]


def site_yaml_subdirectory(site: SiteConfig) -> ConfigBucket:
    """Target folder for a new site file under ``config/sites/{movies,series}/``."""
    c = (site.category or "").strip().lower()
    if c in {"episodes", "updates"}:
        return "series"
    return "movies"


def load_site_yaml(path: Path, name: str | None = None) -> SiteConfig:
    """
    Load a single-site YAML file (not the legacy multi-site `sites:` wrapper).
    """
    site_name = name or path.stem
    with path.open("r", encoding="utf-8") as f:
        raw: Dict[str, Any] = yaml.safe_load(f) or {}
    if "sites" in raw:
        raise ValueError(f"{path}: expected single-site file without top-level 'sites' key")
    return _raw_dict_to_site(site_name, raw)


def load_config(path: Path) -> Config:
    """
    Load configuration from either:
    - a directory using ``movies/*.yaml`` and ``series/*.yaml`` (partitioned layout), or
      legacy flat ``*.yaml`` files in that directory (filename stem = site id), or
    - a legacy single file containing a top-level ``sites:`` mapping.
    """
    if path.is_dir():
        sites: List[SiteConfig] = []
        for child, bucket in discover_site_yaml_files(path):
            site = load_site_yaml(child)
            if bucket is not None:
                site = replace(site, config_bucket=bucket)
            sites.append(site)
        return Config(sites=sites)

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_sites: Dict[str, Dict[str, Any]] = data.get("sites", {})
    if raw_sites:
        sites = [_raw_dict_to_site(name, cfg) for name, cfg in raw_sites.items()]
        return Config(sites=sites)

    # Single-site file without ``sites:`` wrapper
    return Config(sites=[load_site_yaml(path)])


def _raw_dict_to_site(name: str, cfg: Dict[str, Any]) -> SiteConfig:
    detail_raw = cfg.get("detail_method")
    schedule_raw = cfg.get("schedule")
    schedule_cron: Optional[str] = None
    if isinstance(schedule_raw, str) and schedule_raw.strip():
        schedule_cron = schedule_raw.strip()
    elif isinstance(schedule_raw, dict):
        c = schedule_raw.get("cron")
        if isinstance(c, str) and c.strip():
            schedule_cron = c.strip()

    return SiteConfig(
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
        blocked_final_hosts=[str(host) for host in cfg.get("blocked_final_hosts", [])],
        allowed_final_hosts=[str(host) for host in cfg.get("allowed_final_hosts", [])],
        allow_empty_title=bool(cfg.get("allow_empty_title", False)),
        detail_method=_normalize_fetch_method(detail_raw) if detail_raw else None,
        detail_title_selector=cfg.get("detail_title_selector"),
        detail_description_selector=cfg.get("detail_description_selector"),
        max_items=cfg.get("max_items"),
        playwright_wait_selector=cfg.get("playwright_wait_selector"),
        schedule_cron=schedule_cron,
    )


def _normalize_fetch_method(method: str) -> FetchMethod:
    normalized = str(method).strip().lower()
    if normalized == "httpx":
        return "http"
    if normalized in {"http", "cloudscraper", "playwright"}:
        return cast(FetchMethod, normalized)
    return "http"
