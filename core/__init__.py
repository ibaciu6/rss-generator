"""
Core package for the RSS generator platform.

Exposes high‑level interfaces for configuration, scraping, parsing, deduplication,
and feed generation.
"""

from .config import Config, SiteConfig, load_config

__all__ = ["Config", "SiteConfig", "load_config"]
