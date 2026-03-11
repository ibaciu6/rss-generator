from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml


FetchMethod = Literal["http", "cloudscraper", "playwright"]


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
    description_selector: Optional[str] = None
    date_selector: Optional[str] = None
    feed_file: str = "feed.xml"
    category: Optional[str] = None
    fallback_urls: List[str] = field(default_factory=list)
    blocked_content_markers: List[str] = field(default_factory=list)
    blocked_final_hosts: List[str] = field(default_factory=list)
    allowed_final_hosts: List[str] = field(default_factory=list)
    allow_empty_title: bool = False
    detail_method: Optional[FetchMethod] = None
    detail_title_selector: Optional[str] = None
    detail_description_selector: Optional[str] = None
    max_items: Optional[int] = None


@dataclass(frozen=True)
class Config:
    """
    Root configuration model for all sites.
    """

    sites: List[SiteConfig]


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
                method=cfg.get("method", "http"),
                item_selector=str(cfg["item_selector"]),
                title_selector=str(cfg["title_selector"]),
                link_selector=str(cfg["link_selector"]),
                description_selector=cfg.get("description_selector"),
                date_selector=cfg.get("date_selector"),
                feed_file=str(cfg.get("feed_file", f"{name}.xml")),
                category=cfg.get("category"),
                fallback_urls=[str(url) for url in cfg.get("fallback_urls", [])],
                blocked_content_markers=[
                    str(marker) for marker in cfg.get("blocked_content_markers", [])
                ],
                blocked_final_hosts=[str(host) for host in cfg.get("blocked_final_hosts", [])],
                allowed_final_hosts=[str(host) for host in cfg.get("allowed_final_hosts", [])],
                allow_empty_title=bool(cfg.get("allow_empty_title", False)),
                detail_method=cfg.get("detail_method"),
                detail_title_selector=cfg.get("detail_title_selector"),
                detail_description_selector=cfg.get("detail_description_selector"),
                max_items=cfg.get("max_items"),
            )
        )

    return Config(sites=sites)
