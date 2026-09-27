"""Tests for scripts/enrichers/ad_remover.py.

The important invariant here is that aggressive escalation removes *theme
chrome* without touching content. remove_ads_and_boilerplate() runs on the
ALREADY-extracted article body, so a container selector in the aggressive set
(".wrapper", ".article-body") deletes exactly the text that was just extracted.
These tests guard both directions of that rule.
"""
from __future__ import annotations

import pytest

from scripts.enrichers.ad_remover import (
    AGGRESSIVE_AD_SELECTORS,
    DEFAULT_AD_SELECTORS,
    _get_stronger_ad_selectors,
    remove_ads_and_boilerplate,
)

# Selectors that identify a content wrapper rather than page chrome. None may
# ever appear in the aggressive set.
CONTENT_CONTAINERS = (
    ".wrapper",
    ".container",
    ".content-area",
    ".main-area",
    ".article-body",
    "article",
    "main",
    "body",
)

# A boilerplate block that only aggressive mode is meant to strip.
SHARE_BAR = (
    '<div class="sharedaddy">'
    '<a class="share-button" href="#">Tweet</a></div>'
)
ARTICLE_TEXT = "<p>The actual review text that must survive.</p>"


class TestAggressiveSelectorSet:
    def test_aggressive_set_contains_no_content_containers(self):
        """Regression guard: the original stronger set was all containers."""
        for selector in AGGRESSIVE_AD_SELECTORS:
            assert selector not in CONTENT_CONTAINERS, (
                f"{selector!r} is a content container; removing it would "
                "delete extracted article text"
            )

    def test_stronger_set_is_a_superset_of_defaults(self):
        stronger = _get_stronger_ad_selectors()
        assert set(DEFAULT_AD_SELECTORS) <= set(stronger)
        assert set(AGGRESSIVE_AD_SELECTORS) <= set(stronger)

    def test_aggressive_set_has_no_duplicates(self):
        assert len(AGGRESSIVE_AD_SELECTORS) == len(set(AGGRESSIVE_AD_SELECTORS))


class TestAggressiveEscalation:
    def test_aggressive_removes_boilerplate(self):
        html = f"<div>{SHARE_BAR}{ARTICLE_TEXT}</div>"
        cleaned = remove_ads_and_boilerplate(html, aggressive=True)
        assert "sharedaddy" not in cleaned
        assert "Tweet" not in cleaned

    def test_non_aggressive_keeps_boilerplate(self):
        """Proves the escalation branch actually does something."""
        html = f"<div>{SHARE_BAR}{ARTICLE_TEXT}</div>"
        cleaned = remove_ads_and_boilerplate(html, aggressive=False)
        assert "sharedaddy" in cleaned

    def test_aggressive_preserves_nested_containers(self):
        """The original landmine: containers survive into extracted content."""
        html = (
            '<div class="wrapper"><div class="container">'
            f"{ARTICLE_TEXT}</div></div>"
        )
        for aggressive in (True, False):
            cleaned = remove_ads_and_boilerplate(html, aggressive=aggressive)
            assert "The actual review text" in cleaned, (
                f"aggressive={aggressive} destroyed extracted content"
            )

    @pytest.mark.parametrize("aggressive", [True, False])
    def test_default_ad_selectors_always_applied(self, aggressive):
        html = '<div class="advertisement">ad</div><p>keep me</p>'
        cleaned = remove_ads_and_boilerplate(html, aggressive=aggressive)
        assert "advertisement" not in cleaned
        assert "keep me" in cleaned

    @pytest.mark.parametrize("aggressive", [True, False])
    def test_extra_selectors_honored_in_both_modes(self, aggressive):
        html = '<aside class="site-specific">x</aside><p>keep me</p>'
        cleaned = remove_ads_and_boilerplate(
            html, extra_selectors=[".site-specific"], aggressive=aggressive
        )
        assert "site-specific" not in cleaned
        assert "keep me" in cleaned

    def test_invalid_selector_does_not_abort(self):
        html = f"<div>{SHARE_BAR}{ARTICLE_TEXT}</div>"
        cleaned = remove_ads_and_boilerplate(
            html, extra_selectors=["::::bad::selector"], aggressive=True
        )
        assert "The actual review text" in cleaned


class TestExtractedContentSurvives:
    """End-to-end: extract then clean, the way article_enricher.py calls it."""

    def test_extract_then_aggressive_clean_keeps_text(self):
        from scripts.enrichers.article_enricher import extract_main_content

        page = (
            "<html><body><article class='article-body'>"
            "<div class='wrapper'><div class='container'>"
            "<p>Deeply nested review text.</p></div></div>"
            "<div class='sharedaddy'><a href='#'>Tweet</a></div>"
            "</article></body></html>"
        )
        extracted = extract_main_content(page, max_length=50_000)
        cleaned = remove_ads_and_boilerplate(extracted, aggressive=True)

        assert "Deeply nested review text." in cleaned
        assert "Tweet" not in cleaned
