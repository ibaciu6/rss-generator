"""
Core package for the RSS generator platform.

Exposes high‑level interfaces for configuration, scraping, parsing, deduplication,
and feed generation.
"""

from .config import SiteConfig, Config, load_config

__all__ = ["SiteConfig", "Config", "load_config"]
