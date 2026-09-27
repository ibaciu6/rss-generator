"""Tests for scripts/enrichers/ad_remover.py.

The important invariant here is that aggressive escalation removes *theme
chrome* without touching content. remove_ads_and_boilerplate() runs on the
ALREADY-extracted article body, so a container selector in the aggressive set
(".wrapper", ".article-body") deletes exactly the text that was just extracted.
These tests guard both directions of that rule.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from core.config import load_config
from scripts.enrichers.ad_remover import (
    AGGRESSIVE_AD_SELECTORS,
    DEFAULT_AD_SELECTORS,
    _get_stronger_ad_selectors,
    extract_main_content,
    remove_ads_and_boilerplate,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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


# ---- site ad_selectors ------------------------------------------------------
# Per-site promotional blocks, declared in config/sites.yaml rather than added to
# the global sets. A blog theme can wrap anything in <article>, so the fix has to
# be narrow and site-scoped rather than a new global rule.


class TestSiteScopedSelectors:
    """`ad_selectors` in sites.yaml is appended to DEFAULT_AD_SELECTORS (applied
    unconditionally by the enricher), so a site list only ever *adds* removals."""

    # The fallback in core/config.py. A site that declares ad_selectors replaces
    # that fallback, so it must not silently drop any of it.
    CONFIG_DEFAULTS = (
        ".ad",
        ".ad-container",
        ".advertisement",
        "#sidebar",
        ".sidebar",
        ".social-share",
        ".comments",
        ".related-posts",
    )

    def test_site_selectors_keep_the_config_defaults(self):
        config = load_config(REPO_ROOT / "config" / "sites.yaml")
        for site in config.sites:
            missing = [s for s in self.CONFIG_DEFAULTS if s not in site.ad_selectors]
            assert not missing, f"{site.name} drops default ad selectors: {missing}"

    def test_site_selectors_are_compound_not_bare_tags(self):
        """A bare `aside`/`div` in a site's ad_selectors would delete article
        content the moment that site redesigned. Require a class or id."""
        config = load_config(REPO_ROOT / "config" / "sites.yaml")
        for site in config.sites:
            for selector in site.ad_selectors:
                assert any(ch in selector for ch in ".#["), (
                    f"{site.name}: {selector!r} is a bare tag selector"
                )

    def test_site_selectors_are_valid_css(self):
        config = load_config(REPO_ROOT / "config" / "sites.yaml")
        for site in config.sites:
            for selector in site.ad_selectors:
                soup = BeautifulSoup("<div></div>", "html.parser")
                try:
                    soup.select(selector)
                except Exception as exc:
                    pytest.fail(f"{site.name}: invalid selector {selector!r}: {exc}")


# A trimmed reproduction of the gabrielursan.ro theme: the <article> element
# wraps four promotional <aside> siblings of the real body.
GU_ASIDES = (
    '<aside class="promo-articol"><b>Publicitate</b>'
    "<p>Obține până la 200€ recompense pe Kraken.</p></aside>"
    '<aside class="abonare-articol"><p>Vrei să înveți să folosești AI-ul?</p></aside>'
    '<aside class="card-autor coloana dezvaluie">'
    "<p>Autor Gabriel Ursan scrie despre tehnologie.</p></aside>"
    '<aside class="promo" id="promo-curs"> curs </aside>'
)
GU_BODY = "<p>Cele 181 de tabele din WordPress.</p>"


def _gu_article() -> str:
    return f"<article>{GU_ASIDES}{GU_BODY}</article>"


def _gabriel_selectors() -> list[str]:
    config = load_config(REPO_ROOT / "config" / "sites.yaml")
    return next(s.ad_selectors for s in config.sites if s.name == "gabriel-ursan")


class TestGabrielUrsanPromos:
    def test_extraction_keeps_the_promos(self):
        """Regression guard: extraction alone cannot fix this, because they sit
        inside the <article> element. If this ever starts passing, the theme
        changed and the site ad_selectors should be revisited."""
        out = extract_main_content(_gu_article())
        assert "Publicitate" in out

    @pytest.mark.parametrize(
        "probe",
        ["Publicitate", "200€", "Vrei să înveți", "Autor Gabriel Ursan", "promo-curs"],
    )
    def test_promos_are_removed_with_the_site_selectors(self, probe):
        out = remove_ads_and_boilerplate(
            extract_main_content(_gu_article()),
            extra_selectors=_gabriel_selectors(),
        )
        assert probe not in out

    def test_article_body_survives(self):
        out = remove_ads_and_boilerplate(
            extract_main_content(_gu_article()),
            extra_selectors=_gabriel_selectors(),
        )
        assert "181 de tabele" in out

    def test_no_aside_markup_left_at_all(self):
        out = remove_ads_and_boilerplate(
            extract_main_content(_gu_article()),
            extra_selectors=_gabriel_selectors(),
        )
        assert "<aside" not in out
