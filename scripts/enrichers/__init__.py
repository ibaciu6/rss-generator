"""Enrichment modules for different feed types."""
from __future__ import annotations

from scripts.enrichers.ad_remover import DEFAULT_AD_SELECTORS, remove_ads_and_boilerplate
from scripts.enrichers.article_enricher import enrich_article_feed
from scripts.enrichers.streaming_enricher import process_feed as enrich_streaming_feed

__all__ = [
    "DEFAULT_AD_SELECTORS",
    "enrich_article_feed",
    "enrich_streaming_feed",
    "remove_ads_and_boilerplate",
]