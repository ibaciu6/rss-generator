from __future__ import annotations

from dataclasses import dataclass
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
            )
        )

    return Config(sites=sites)

