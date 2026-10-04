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
    extract_featured_image,
    extract_main_content,
    remove_ads_and_boilerplate,
    truncate_content,
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
# A trimmed reproduction of the gabrielursan.ro theme. Only one child of
# <article> is the article; everything else is chrome that used to ship in the
# feed, including a <header> that repeats the H1 the reader already shows.
GU_TITLE = "De ce am plecat de pe WordPress după 15 ani"
GU_PAGE = f"""<html><head>
<title>{GU_TITLE}</title>
<meta property="og:image" content="https://gabrielursan.ro/api/media/file/coperta.png">
</head><body><main><div><div>
<article>
  <header class="cap coloana">
    <a class="chip">General</a>
    <h1>{GU_TITLE}</h1>
    <p class="meta byline">de Gabriel Ursan · 13 august 2026 · 9 minute de citit</p>
    <nav class="partajare compacta" aria-label="Distribuie articolul">
      <a href="#">WhatsApp</a><a href="#">Facebook</a></nav>
  </header>
  <figure class="coperta coloana-larga"><img src="/coperta.png" alt=""></figure>
  <div class="articol-continut coloana">
    <p>Cele 181 de tabele din WordPress.</p>
    <p>Migrarea a durat cinci nopți, iar cea mai grea nu a fost exportul, ci
      păstrarea legăturilor interne după schimbarea schemei de adrese.</p>
    <h2>Comentariile s-au întors</h2>
    <p>Acum poți comenta din nou. Comentariile sunt ale mele, nu ale nimănui
      altcuiva: nu e Disqus, nu e niciun script încărcat de la terți.</p>
    <p>Din cele 24.863 de comentarii vechi am păstrat firul, autorul și data,
      iar după migrare fiecare articol vechi își are în continuare pagina
      corectă, cu aceeași ancoră în adresă, deci linkurile din exterior
      continuă să funcționeze fără nicio redirecție.</p>
    <p>Statisticile le-am ținut tot pe ale mele, pentru că nu vreau ca un
      contor extern să învețe cine vine pe blog și ce citește.</p>
  </div>
  <nav class="partajare coloana" aria-label="Distribuie articolul">
    <a href="#">LinkedIn</a></nav>
  <nav class="taguri coloana"><a href="#">comunicate presa</a></nav>
  <aside class="card-autor coloana dezvaluie">Autor Gabriel Ursan scrie despre tehnologie.</aside>
  <nav class="vecini coloana">← Mai vechi Restaurant tradițional</nav>
  <section class="inscriere">Învață AI în 21 de zile, gratuit</section>
  <aside class="promo" id="promo-curs">curs</aside>
  <aside class="promo-articol"><b>Publicitate</b><p>Obține până la 200€ pe Kraken.</p></aside>
  <section class="comentarii coloana">Comentarii Lasă un comentariu</section>
</article>
</div></div></main></body></html>"""


def _gu_site():
    config = load_config(REPO_ROOT / "config" / "sites.yaml")
    return next(s for s in config.sites if s.name == "gabriel-ursan")


def _gu_extract() -> str:
    """Run the real two-stage pipeline the enricher uses, with the site's config."""
    site = _gu_site()
    body = extract_main_content(
        GU_PAGE,
        article_selectors=[site.detail_article_selector] if site.detail_article_selector else None,
    )
    return remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)


def _gu_text(html: str) -> str:
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())


class TestGabrielUrsanExtraction:
    """gabriel-ursan needs a detail_article_selector: its <article> wraps the
    body in a pile of theme chrome, and the reader renders the title itself, so
    the H1 inside the body is a visible duplicate."""

    def test_detail_selector_is_configured(self):
        assert _gu_site().detail_article_selector == ".articol-continut"

    def test_extraction_keeps_the_chrome(self):
        """Regression guard: extraction alone cannot fix this. If this ever
        starts passing, the theme changed and the site config should be revisited."""
        raw = _gu_text(extract_main_content(GU_PAGE))
        for probe in ("minute de citit", "Publicitate", "Lasă un comentariu", "Mai vechi"):
            assert probe in raw, probe

    @pytest.mark.parametrize(
        "probe",
        [
            "Distribuie articolul",       # share navs
            "minute de citit",            # byline / read time
            "Publicitate",                # referral ad
            "200€",                       # ad body
            "Lasă un comentariu",         # comment form
            "Mai vechi",                  # prev/next nav
            "21 de zile",                 # newsletter pitch
            "scrie despre tehnologie",    # author card
            "curs",                       # course promo
        ],
    )
    def test_chrome_is_gone(self, probe):
        assert probe not in _gu_text(_gu_extract())

    def test_title_is_not_duplicated(self):
        """The reader prints the title above the body, so an H1 inside the body
        is the same headline twice in a row."""
        html = _gu_extract()
        assert "<h1" not in html
        assert GU_TITLE not in _gu_text(html)

    def test_real_article_text_survives(self):
        text = _gu_text(_gu_extract())
        assert "181 de tabele" in text
        assert "Comentariile s-au întors" in text, "an H2 that happens to say 'comentarii' is content"

    def test_no_navigation_or_aside_markup_remains(self):
        html = _gu_extract()
        for tag in ("<nav", "<aside", "<header"):
            assert tag not in html, tag

    def test_body_is_still_substantial(self):
        """Guards against a selector change that empties the body: the fallback
        path would then ship the whole cleaned page instead."""
        assert len(_gu_text(_gu_extract())) > 500

    def test_featured_image_survives_outside_the_content_div(self):
        """The cover <figure> is a sibling of the content div, so extraction drops
        it - the featured image has to come from og:image instead."""
        from scripts.enrichers.ad_remover import extract_featured_image

        assert extract_featured_image(GU_PAGE) == "https://gabrielursan.ro/api/media/file/coperta.png"
        assert "<img" not in _gu_extract()



class TestGabrielUrsanFallbackPath:
    """`extract_main_content` falls back to cleaning the whole page when its
    selector matches nothing. That path has to stay usable, which is what the
    nav/section selectors in ad_selectors are for - `detail_article_selector`
    alone would leave the fallback shipping the entire page."""

    def test_whole_page_fallback_is_still_clean(self):
        site = _gu_site()
        text = _gu_text(remove_ads_and_boilerplate(GU_PAGE, extra_selectors=site.ad_selectors))
        for probe in ("Distribuie articolul", "Mai vechi", "Lasă un comentariu",
                      "21 de zile", "Publicitate", "200€", "scrie despre tehnologie"):
            assert probe not in text, probe

    def test_fallback_keeps_the_article_text(self):
        site = _gu_site()
        text = _gu_text(remove_ads_and_boilerplate(GU_PAGE, extra_selectors=site.ad_selectors))
        assert "181 de tabele" in text
        assert "24.863 de comentarii vechi" in text

    def test_every_chrome_selector_is_class_scoped(self):
        """The whole point of naming these individually: a bare `nav` or
        `section` would strip article content the moment one appears in a post."""
        site = _gu_site()
        for selector in ("nav.partajare", "nav.taguri", "nav.vecini",
                         "section.inscriere", "section.comentarii"):
            assert selector in site.ad_selectors, selector


class TestTruncateContent:
    """`truncate_content` must actually enforce its cap.

    It used to trim only the single text node that crossed the limit and then
    stop, leaving the rest of the document in place. A page dominated by one huge
    text node (a `<style>` block) therefore sailed past the cap, and the
    `len(cleaned) <= MAX_DESCRIPTION_LENGTH` check in article_enricher rejected
    the item - which is how hoinaru ended up with 10/10 excerpts.
    """

    def test_short_content_is_untouched(self):
        html = "<p>un articol scurt.</p>"
        assert truncate_content(html, 50_000) == html

    def test_text_is_capped(self):
        html = "<p>" + ("a" * 100) + "</p><p>" + ("b" * 100) + "</p>"
        out = truncate_content(html, 50)
        assert len(_gu_text(out)) <= 50

    def test_content_after_the_cap_is_dropped(self):
        """The failure mode: trimming one node and keeping the rest."""
        html = "<p>" + ("a" * 100) + "</p><p>NU TREBUIE SA APAREA</p>"
        out = truncate_content(html, 50)
        assert "NU TREBUIE" not in _gu_text(out)

    def test_a_single_huge_text_node_does_not_defeat_the_cap(self):
        """A `<style>` block is one text node; the old code trimmed it and kept
        everything after it."""
        html = "<style>" + ("x" * 1000) + "</style><p>continutul articolului</p>"
        out = truncate_content(html, 100)
        assert "continutul articolului" not in _gu_text(out)
        assert len(_gu_text(out)) <= 100

    def test_truncation_lands_inside_the_crossing_node(self):
        """The cap cuts mid-node: the first `max_chars` chars are kept."""
        html = "<p>" + ("a" * 30) + "MIDDLE" + ("a" * 30) + "</p>"
        out = truncate_content(html, 40)
        assert _gu_text(out) == ("a" * 30 + "MIDDLE" + "a" * 30)[:40]


# A trimmed reproduction of the hoinaru.ro WordPress/WPBakery theme. The page is
# dominated by a <style> block, so extraction without a detail selector returns
# the whole document - and lands just over MAX_DESCRIPTION_LENGTH, which made
# every item skip and the feed ship excerpts.
HU_PAGE = """<html><head>
<style>img.wp-smiley, img.emoji {{ display: inline !important; border: none !important; }}</style>
</head><body><main class="l-main">
<div class="l-section-h"><div class="g-cols type_default valign_top">
<div class="vc_col-sm-9 vc_column_container l-content"><div class="vc_column-inner">
<div class="wpb_wrapper">
  <div class="w-post-elm post_content">
    <p>Am fost în Scoția în vacanță, am descoperit niște locuri minunate, apoi am
      revenit acasă și ne-am schimbat hainele groase din bagaj cu unele de vară.</p>
    <p>Și pe drum am rămas fără aer condiționat la mașină. Am sunat și la service,
      cu speranța că mă pot ajuta, dar nu prea a funcționat.</p>
    <p>Am prins trafic destul de mare în defileul Kresna, bine că am plecat
      devreme și am reușit să trec de acel punct înainte să se aglomereze.</p>
    <p>Care era problema: îngheață vaporizatorul, probabil un termostat defect,
      deși mașina avea doar 90.000 km și nu ar fi trebuit să dea semne de oboseală.</p>
    <p>Și totul cu 3 prompturi. Nu cu unul singur, ci cu trei, pentru că primul
      răspuns a fost prea general și nu ajuta la nimic concret.</p>
    <div class="crp_related crp-text-only">Pe aceeaşi temă: Vacanţă în Lefkada (2026)
      O poveste cu ulei de măsline din Lefkada</div>
  </div>
  <div class="w-separator size_custom"></div>
  <div class="w-sharing type_solid align_left colo">Distribuie articolul</div>
</div>
</div></div></div></div>
</main></body></html>"""


class TestHoinaruExtraction:
    """hoinaru needs a detail_article_selector: its page is one big <style> block
    plus WPBakery chrome, and extraction without a selector returned the whole
    document - just over MAX_DESCRIPTION_LENGTH, so all 10 items were skipped."""

    def test_detail_selector_is_configured(self):
        assert _hu_site().detail_article_selector == "div.w-post-elm.post_content"

    def test_extraction_without_the_selector_grabs_the_style_block(self):
        """Regression guard: this is what made every item skip. If this ever
        stops matching, the theme changed and the config should be revisited.
        Assert on the HTML, not the text: get_text() drops <style> content."""
        raw = extract_main_content(HU_PAGE)
        assert "<style" in raw
        assert "img.wp-smiley" in raw

    def test_extraction_with_the_selector_is_clean(self):
        site = _hu_site()
        body = extract_main_content(
            HU_PAGE,
            article_selectors=[site.detail_article_selector],
        )
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        text = _gu_text(out)
        assert "Am fost în Scoția" in text
        assert "3 prompturi" in text

    def test_no_theme_chrome_survives(self):
        site = _hu_site()
        body = extract_main_content(
            HU_PAGE,
            article_selectors=[site.detail_article_selector],
        )
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        for probe in ("img.wp-smiley", "crp_related", "w-sharing", "wpb_", "vc_column"):
            assert probe not in out, probe

    def test_related_posts_block_is_dropped(self):
        site = _hu_site()
        body = extract_main_content(
            HU_PAGE,
            article_selectors=[site.detail_article_selector],
        )
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        assert "Pe aceeaşi temă" not in _gu_text(out)

    def test_body_is_still_substantial(self):
        site = _hu_site()
        body = extract_main_content(
            HU_PAGE,
            article_selectors=[site.detail_article_selector],
        )
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        assert len(_gu_text(out)) > 500


def _hu_site():
    config = load_config(REPO_ROOT / "config" / "sites.yaml")
    return next(s for s in config.sites if s.name == "hoinaru")


# A trimmed reproduction of the manafu.ro WordPress theme. .content wraps the title,
# byline, share buttons and tags around the body, so extraction without a detail
# selector shipped the headline twice plus the theme chrome.
MF_TITLE = "Brazilia testeaza un model de monetizare a datelor digitale"
MF_PAGE = f"""<html><head>
<meta property="og:image" content="https://www.manafu.ro/wp-content/uploads/2025/06/dwallet-brazilia.jpg">
</head><body><div class="post">
  <h2>{MF_TITLE}</h2>
  <ul class="subhead clearfix"><li>Cristian Manafu</li><li>03.06.2025</li><li>Comenează</li></ul>
  <div class="entry">
    <p>Brazilia a lansat un proiet pilot inovator care permite cetatenilor sa
      isi gestioneze, detina si monetizeze datele digitale printr-un sistem numit
      dWallet. Programul ofera utilizatorilor posibilitatea de a stoca datele
      generate de activitatile lor online intr-un cont de economii pentru date.</p>
    <p>Programul este administrat de Dataprev, o companie de stat braziliana, si
      realizat in colaborare cu DrumWave, o firma din California axata pe evaluarea
      si monetizarea datelor.</p>
    <h3>Cum functioneaza dWallet?</h3>
    <p>Practic, fiecare cetatean isi poate deschise un cont de economii cu date,
      unde se stocheaza informatiile generate de activitatea sa online.</p>
    <p>Daca acest model va avea succes, ar putea deschide calea pentru o noua
      economie digitala globala, in care utilizatorii nu doar ca isi protejeaza
      datele, ci si profita de pe urma lor.</p>
    <div class="crp_related crp-text-only">Articole similare: Rețeaua socială a
      Europei va fi lansată din România Aleph și Reddit redesenează harta
      publicității digitale din România</div>
  </div>
  <div class="social-btn-group">Distribuie articolul</div>
  <div class="metapost nobb nobg clearfix">Tags: Brazilia, Date Personale, Digital Marketing</div>
</div></body></html>"""


class TestManafuExtraction:
    """manafu needs a detail_article_selector: its .content wrapper holds the
    title, byline, share buttons and tags around the body, so the reader showed
    the headline twice and carried the theme chrome."""

    def test_detail_selector_is_configured(self):
        assert _mf_site().detail_article_selector == ".entry"

    def test_extraction_without_the_selector_grabs_the_title_header(self):
        """Regression guard: this is what duplicated the title. Assert on the
        HTML, not the text - get_text() drops <style> content."""
        raw = extract_main_content(MF_PAGE)
        assert "<h2" in raw
        assert MF_TITLE in _gu_text(raw)

    def test_extraction_with_the_selector_is_clean(self):
        site = _mf_site()
        body = extract_main_content(MF_PAGE, article_selectors=[site.detail_article_selector])
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        text = _gu_text(out)
        assert "Brazilia a lansat" in text
        assert "profita de pe urma lor" in text

    def test_no_theme_chrome_survives(self):
        site = _mf_site()
        body = extract_main_content(MF_PAGE, article_selectors=[site.detail_article_selector])
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        for probe in ("Cristian Manafu", "Comenează", "Articole similare", "Distribuie",
                      "Tags:", "cancel reply", "<h2"):
            assert probe not in out, probe

    def test_title_is_not_duplicated(self):
        site = _mf_site()
        body = extract_main_content(MF_PAGE, article_selectors=[site.detail_article_selector])
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        assert MF_TITLE not in _gu_text(out)

    def test_related_posts_block_is_dropped(self):
        site = _mf_site()
        body = extract_main_content(MF_PAGE, article_selectors=[site.detail_article_selector])
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        assert "Articole similare" not in _gu_text(out)

    def test_body_is_still_substantial(self):
        site = _mf_site()
        body = extract_main_content(MF_PAGE, article_selectors=[site.detail_article_selector])
        out = remove_ads_and_boilerplate(body, extra_selectors=site.ad_selectors)
        assert len(_gu_text(out)) > 500


def _mf_site():
    config = load_config(REPO_ROOT / "config" / "sites.yaml")
    return next(s for s in config.sites if s.name == "manafu")


class TestFeaturedImageSkipsJunk:
    """The featured-image fallback picks the first image in the article. When
    that image is a Facebook emoji (razvanbb) or a placeholder SVG, it must be
    skipped -- otherwise the emoji gets prepended as the article's featured
    image."""

    def test_emoji_is_not_returned_as_featured(self):
        page = (
            '<html><head></head><body><article>'
            '<img src="https://static.xx.fbcdn.net/images/emoji.php/v9/x.png">'
            '<img src="https://www.manafu.ro/wp-content/uploads/2026/09/real.jpg">'
            "</article></body></html>"
        )
        assert extract_featured_image(page) == "https://www.manafu.ro/wp-content/uploads/2026/09/real.jpg"

    def test_placeholder_svg_is_not_returned_as_featured(self):
        page = (
            '<html><head></head><body><article>'
            '<img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMSI+">'
            '<img src="https://www.manafu.ro/wp-content/uploads/2026/09/real.jpg">'
            "</article></body></html>"
        )
        assert extract_featured_image(page) == "https://www.manafu.ro/wp-content/uploads/2026/09/real.jpg"

    def test_og_image_emoji_is_skipped(self):
        page = (
            '<html><head>'
            '<meta property="og:image" content="https://static.xx.fbcdn.net/x/emoji.png">'
            '</head><body><article>'
            '<img src="https://www.manafu.ro/wp-content/uploads/2026/09/real.jpg">'
            "</article></body></html>"
        )
        assert extract_featured_image(page) == "https://www.manafu.ro/wp-content/uploads/2026/09/real.jpg"

    def test_real_og_image_is_still_returned(self):
        page = (
            '<html><head>'
            '<meta property="og:image" content="https://www.manafu.ro/wp-content/uploads/2026/09/og.jpg">'
            '</head><body><article></article></body></html>'
        )
        assert extract_featured_image(page) == "https://www.manafu.ro/wp-content/uploads/2026/09/og.jpg"


class TestFeaturedImageSkipsThemeAssets:
    """The featured image is prepended after the removal modules run, so a
    theme asset selected as og:image lands in the feed even when the
    `theme-icons` module is active."""

    @pytest.mark.parametrize("src", [
        "https://www.schneier.com/wp-content/themes/schneier/assets/images/rss.png",
        "https://securelist.com/wp-content/themes/securelist2020/assets/images/avatar-default/avatar_default_1.png",
        "https://snoop.ro/wp-content/themes/snoop/public/images/icon-google.d418db.svg",
    ])
    def test_theme_asset_og_image_is_skipped(self, src):
        page = (f'<html><head><meta property="og:image" content="{src}"></head>'
                '<body><article><img src="https://x.ro/wp-content/uploads/2026/09/real.jpg">'
                "</article></body></html>")
        assert extract_featured_image(page) == "https://x.ro/wp-content/uploads/2026/09/real.jpg"

    def test_theme_asset_first_image_is_skipped(self):
        page = ('<html><body><article>'
                '<img src="https://x.ro/wp-content/themes/x/images/rss.png">'
                '<img src="https://x.ro/wp-content/uploads/2026/09/real.jpg">'
                "</article></body></html>")
        assert extract_featured_image(page) == "https://x.ro/wp-content/uploads/2026/09/real.jpg"

    def test_no_featured_image_when_only_theme_assets_exist(self):
        page = ('<html><body><article>'
                '<img src="https://x.ro/wp-content/themes/x/images/rss.png">'
                "</article></body></html>")
        assert extract_featured_image(page) is None


class TestExtractPicksTheCandidateHoldingTheProse:
    """`elements[0]` is the wrong choice whenever a selector fits several
    elements, and it fails silently: the first match is returned, the better
    candidate is never tried, and the caller sees a plausible short string.

    hackread's page has 13 `<article>` tags -- sidebar and related-post cards
    of ~290 characters each -- and a `.entry-content` holding the 3,833-char
    article. The old code took the first `<article>` and left every one of its
    items a 68-character excerpt.
    """

    def test_a_later_larger_candidate_wins_over_an_earlier_small_one(self):
        html = (
            "<html><body>"
            '<article class="card"><p>Related post teaser card.</p></article>'
            '<div class="entry-content"><p>' + "The real article. " * 40 + "</p></div>"
            "</body></html>"
        )
        out = extract_main_content(html)
        assert "The real article." in out
        assert "Related post teaser" not in out

    def test_a_single_candidate_is_unaffected(self):
        html = "<html><body><article><p>Only one article here.</p></article></body></html>"
        assert "Only one article here." in extract_main_content(html)

    def test_selector_order_breaks_a_tie(self):
        html = (
            "<html><body>"
            '<div class="content">generic</div>'
            "<article>same length</article>"
            "</body></html>"
        )
        # `article` is listed first, so it wins an exact tie.
        assert "same length" in extract_main_content(html)

    def test_an_explicit_selector_is_honoured(self):
        html = (
            "<html><body>"
            '<div id="pick-me">short</div>'
            '<div class="entry-content">much longer prose that would otherwise win</div>'
            "</body></html>"
        )
        out = extract_main_content(html, ["#pick-me"])
        assert "short" in out and "much longer" not in out

    def test_a_page_matching_nothing_still_falls_back(self):
        html = "<html><body><p>Loose prose with no container at all.</p></body></html>"
        assert "Loose prose" in extract_main_content(html)


class TestCandidateRankingRunsTheRealPipeline:
    """Ranking on the raw subtree picks a candidate that the rest of the
    pipeline then empties, which is worse than the bug it replaced: apador-ch
    went from 8,795 characters of article to 464 characters of "Citeste si"
    related posts, because `.content` (the theme's outer wrapper) outscored the
    real article on the strength of a block the ad selectors then delete.
    """

    def test_a_candidate_the_ad_selectors_empty_does_not_win(self):
        html = (
            "<html><body><div class='content'>"
            '<div class="related-posts">Related posts you did not ask for.</div>'
            "<article>The actual article, which is shorter than its own "
            "related-posts block but is the article.</article>"
            "</div></body></html>"
        )
        out = extract_main_content(html)
        assert "The actual article" in out
        assert "Related posts you did not ask for" not in out

    def test_a_candidate_the_footer_cut_empties_does_not_win(self):
        html = (
            "<html><body><div class='content'>"
            '<div class="related">A long related-posts list. ' * 30 + "</div>"
            "<article>Short but real.</article>"
            "</div></body></html>"
        )
        assert "Short but real." in extract_main_content(html)

    def test_ranking_does_not_consume_the_winner(self):
        """Every candidate is probed with the ad selectors and the footer cut,
        so a probe that mutated the live tree would hand back a winner with
        its own content already deleted. Only the winner is returned; it must
        come back whole."""
        html = (
            "<html><body>"
            '<div class="content"><div class="related-posts">noise</div>'
            "<p>Alpha prose that must survive probing.</p></div>"
            "<article><p>Beta prose that must survive too.</p></article>"
            "</body></html>"
        )
        # `.content` wins on yield; the point is that it comes back whole,
        # with its prose intact and the losing candidate's subtree untouched.
        out = extract_main_content(html)
        assert "Alpha prose that must survive probing." in out
        assert "Beta prose that must survive too." not in out


# --------------------------------------------------------------------------- #
# The extraction the *shipped* config asks for.
#
# Each fixture below is a trimmed reproduction of a theme whose outer wrapper
# outranks the article, and each one names what the generic ladder picked before
# the site was given a `detail_article_selector`. The pages are read from the
# real config rather than hardcoding the selector, so a selector deleted from
# sites.yaml fails here instead of quietly restoring the whole-page extraction.
# --------------------------------------------------------------------------- #

# thehackernews.com is a Blogger blog: `div.post-body` wraps the whole post,
# schema.org tags included, and holds a byline whose two markers are icon-font
# glyphs at U+E802/U+E804. Outside the site those are missing-glyph boxes, so
# every item read "<glyph> Ravie Lakshmanan<glyph>Oct 02, 2026Cyber Espionage".
_THN_BODY = (
    '<div class="articlebody clear cf" id="articlebody">'
    "<p>Antino Backdoor Uses Outlook and OneDrive for C2 in China-Nexus "
    "Espionage Campaign, according to the researchers who found it.</p>"
    '<p>The loader is delivered as a weaponised document.</p>'
    "</div>"
)
_THN_PAGE = (
    "<html><body>"
    '<div class="widget Blog" id="Blog1"><div class="blog-posts clear">'
    '<div class="post"><div class="post-body" itemscope itemtype="http://schema.org/NewsArticle">'
    '<link href="https://thehackernews.com/2026/10/antino.html" itemprop="mainEntityOfPage url">'
    '<meta content="Antino Backdoor" itemprop="headline">'
    '<div class="clear post-head"><div class="postmeta">'
    '<span class="p-author"><i class="icon-font icon-user"></i>'
    '<span class="author">Ravie Lakshmanan</span>'
    '<i class="icon-font icon-calendar"></i>'
    '<span class="author">Oct 02, 2026</span></span>'
    '<span class="p-tags">Cyber Espionage / Malware</span>'
    "</div></div>"
    + _THN_BODY
    + "</div></div></div></div></body></html>"
)

# b365.ro's Strawberry theme names a *related-post card* `.article`, which is one
# of the ladder's selectors. The card is a teaser about a different story, so
# every item shipped someone else's summary -- and, being the only candidate, it
# won every time.
_B365_PROSE = (
    '<div class="single__content">'
    "<p>Orice cină romantică are două etape: drumul spre restaurant și "
    "restaurantul. Primarul general al Capitalei a povestit, la evenimentul "
    "Hora din București, că își scoate soția în oraș cam de două ori pe lună.</p>"
    "<p>Romeo cu caiet de sarcini. Partea romantică e scurtă și eficientă: o "
    "seară aleasă, o masă rezervată, o ieșire în doi.</p>"
    "</div>"
)
_B365_CARD = (
    '<div class="article"><div class="article__content">'
    '<div class="article__eyebrow"><span>LA MINE-N CARTIER</span> | 22.08.2025</div>'
    '<div class="article__excerpt">Dl. Negoiță Avangardistul inovează masiv în S3.</div>'
    '<div class="article__more"><a href="https://b365.ro/alt-articol/">citește mai mult</a></div>'
    "</div></div>"
)
_B365_PAGE = (
    "<html><body>"
    '<div class="single__content_wrapper">'
    '<div class="article__eyebrow">Știri București | 02.10.2026</div>'
    + _B365_PROSE
    + _B365_CARD
    + "</div></body></html>"
)

# theregister.com puts `article` on <body>, so the ladder's `.article` selector
# matches the entire document: masthead, two search boxes, four "TOP STORIES"
# rails and the site footer all outranked the 3,084-character body text.
_REGISTER_TOPLIST_ITEM = (
    "<li><a href='/security/2026/09/29/fbi-to-shinyhunters/5299901'>"
    "<h4>FBI to ShinyHunters: 'We know how to find you'</h4>"
    "<time>4 days ago</time></a></li>"
)
_REGISTER_BODYTEXT = (
    '<div class="bodytext large-12 small-12 medium-12">'
    '<div class="column articlesByTag toplist desktop-floatRight">'
    '<div class="content"><h3 class="t21">READ MORE</h3><ul>'
    + _REGISTER_TOPLIST_ITEM
    + "</ul></div></div>"
    "<p>Asus has warned eShop customers that an intruder got into part of its "
    "online store and may have helped themselves to contact details and order "
    "records, the company said in an email sent to customers.</p>"
    '<div class="column google-ad widthFull">REG AD</div>'
    "<p>The PC maker said it had identified unauthorized access to part of the "
    "Asus eShop environment, although exactly when that occurred remains unclear."
    "</p>"
    # The floated related-story card, which lands mid-article on roughly one
    # item in thirty. extract_main_content hands over the *contents* of
    # div.bodytext, so a selector naming the wrapper cannot reach it.
    '<article class="column desktop-floatLeft small-12 large-2">'
    '<a href="/security/2026/08/14/palo-alto-black-hat/">'
    '<figure class="artwork"><img alt="black hat booth" src="/186694.webp"></figure>'
    '<h2 class="headline" itemprop="headline">Palo Alto Networks execs apologize '
    "for 'hostesses' dressed as lamps at Black Hat booth</h2>"
    '<p class="subtitle" itemprop="description">'
    '<span class="underline">READ MORE</span></p></a></article>'
    "<p>There is at least some good news for anyone who has handed Asus their "
    "card details: no payment card or bank account was involved.</p>"
    "</div>"
)
_REGISTER_PAGE = (
    '<html><body class="l4 article site_theregister section_security">'
    '<a class="skip-link" href="#main">Jump to main content</a>'
    '<header class="pageElement pageHeader"><div class="hamburger">'
    '<form action="/cse" role="search"><label>Search</label>'
    '<input placeholder="Search..." type="text"></form>'
    '<figure class="logo logo--primary"><a aria-label="Go to frontpage">'
    '<img alt="Go to frontpage. Logo, The Register" src="/logo.svg"></a></figure>'
    "</header>"
    '<section id="main" class="main article k5a-article">'
    '<div class="articleHeader column"><h1 class="headline mainTitle">'
    "Someone went shopping in ASUS's eShop</h1></div>"
    + _REGISTER_BODYTEXT
    + '<div class="column articleFooter"><a href="/archive/">27 years of articles</a></div>'
    "</section>"
    '<div class="column articlesByTag toplist small-12 large-4">'
    '<h3>TOP STORIES</h3><ul><li><a href="/x/">AI models keep posting screenshots'
    "</a></li></ul></div>"
    "<footer><nav><a href=\"/about/\">About Us</a>"
    '<a href="/advertise/">Advertise with us</a></nav></footer>'
    "</body></html>"
)

# Enrichment's own extraction + cleanup, so the tests exercise the selector and
# the ad_selectors exactly where the pipeline applies them.
def _extract_as_configured(page: str, site) -> str:
    selectors = [site.detail_article_selector] if site.detail_article_selector else None
    body = extract_main_content(page, article_selectors=selectors, max_length=50_000)
    return remove_ads_and_boilerplate(
        body, extra_selectors=list(site.ad_selectors), aggressive=True
    )


def _configured_site(name: str):
    config = load_config(REPO_ROOT / "config" / "sites.yaml")
    for site in config.sites:
        if site.name == name:
            return site
    raise AssertionError(f"{name} is not in config/sites.yaml")


def _no_selector():
    """The generic ladder: what a site looks like with nothing configured."""

    class _Site:
        detail_article_selector = None
        ad_selectors: list[str] = []

    return _Site()


def _text(html: str) -> str:
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())


class TestTheHackerNewsBodyIsTheArticleAndNotTheByline:
    """The byline's icon-font glyphs are private-use characters (U+E802,
    U+E804). Inside the site they are icons; in a feed they are missing-glyph
    boxes glued to the author name and the date, which is the whole complaint."""

    def test_the_configured_selector_keeps_the_glyphs_out(self):
        site = _configured_site("thehackernews")
        assert site.detail_article_selector, "thehackernews needs a selector"
        out = _extract_as_configured(_THN_PAGE, site)
        assert "" not in out and "" not in out
        assert "Antino Backdoor Uses Outlook" in _text(out)

    def test_the_configured_selector_drops_the_schema_microdata(self):
        out = _extract_as_configured(_THN_PAGE, _configured_site("thehackernews"))
        assert "<meta" not in out and "<link" not in out

    def test_without_the_selector_the_glyphs_and_byline_ship(self):
        """The defect itself, so the fix above cannot pass for the wrong reason:
        the generic ladder takes `div.post-body` and every one of those glyphs
        reaches the feed."""
        out = _extract_as_configured(_THN_PAGE, _no_selector())
        assert "" in out and "" in out
        assert "Ravie Lakshmanan" in _text(out)


class TestB365ExtractsItsOwnArticle:
    def test_the_configured_selector_returns_the_prose(self):
        site = _configured_site("b365")
        assert site.detail_article_selector, "b365 needs a selector"
        out = _extract_as_configured(_B365_PAGE, site)
        assert "Orice cină romantică are două etape" in _text(out)
        assert "Romeo cu caiet de sarcini" in _text(out)

    def test_the_related_post_card_is_not_published_as_the_article(self):
        out = _extract_as_configured(_B365_PAGE, _configured_site("b365"))
        assert "Avangardistul" not in _text(out)
        assert "citește mai mult" not in _text(out)

    def test_without_the_selector_the_ladder_picks_the_card(self):
        """The trap: `.article` is a card about a different story, and on this
        page it is the only candidate, so it wins by default."""
        out = _extract_as_configured(_B365_PAGE, _no_selector())
        assert "citește mai mult" in _text(out)
        assert "Orice cină romantică" not in _text(out)


class TestTheRegisterBodyIsNotTheWholeSite:
    # What the whole-page extraction leaked into all 30 items. "Jump to main
    # content" is deliberately absent: `.skip-link` is in the aggressive set, so
    # it never survives either path and is not evidence of anything.
    CHROME = (
        "About Us",
        "Advertise with us",
        "27 years of articles",
        "TOP STORIES",
        "AI models keep posting screenshots",
        "Someone went shopping in ASUS's eShop",
    )

    def test_the_configured_selector_keeps_the_chrome_out(self):
        site = _configured_site("the-register-security")
        assert site.detail_article_selector, "the-register-security needs a selector"
        out = _extract_as_configured(_REGISTER_PAGE, site)
        for chrome in self.CHROME:
            assert chrome not in _text(out), chrome

    def test_the_article_survives(self):
        out = _extract_as_configured(_REGISTER_PAGE, _configured_site("the-register-security"))
        assert "Asus has warned eShop customers" in _text(out)
        assert "unauthorized access to part of the Asus eShop" in _text(out)

    def test_the_configured_ad_selectors_drop_the_toplist_and_the_ad_slot(self):
        """Both live inside div.bodytext, so the selector alone still leaves the
        "READ MORE" rail and the REG AD box at the top of the article."""
        site = _configured_site("the-register-security")
        assert site.ad_selectors, "the-register-security needs ad_selectors"
        out = _extract_as_configured(_REGISTER_PAGE, site)
        assert "READ MORE" not in _text(out)
        assert "REG AD" not in _text(out)

    def test_the_related_story_card_mid_article_is_dropped(self):
        """Two items in thirty carry a floated related-story card *inside* the
        prose. It has to be caught by its own class: the extracted body is the
        contents of div.bodytext, so `div.bodytext article` matches nothing."""
        out = _extract_as_configured(_REGISTER_PAGE, _configured_site("the-register-security"))
        assert "Palo Alto Networks execs apologize" not in _text(out)

    def test_without_that_selector_the_card_survives_mid_article(self):
        class _NoCardRemoval:
            detail_article_selector = "div.bodytext"
            ad_selectors = [s for s in _configured_site("the-register-security").ad_selectors
                            if s != "article.column"]

        out = _extract_as_configured(_REGISTER_PAGE, _NoCardRemoval())
        assert "Palo Alto Networks execs apologize" in _text(out)
        assert "READ MORE" in _text(out)

    def test_without_the_selector_body_article_wins_and_the_page_ships(self):
        """The defect itself: `<body class="... article ...">` is the whole
        document. On the live page `.article` matches three elements and `<body>`
        outranks them on yield; in the fixture it is the only `.article` match.
        30 real items carried 40-45KB of masthead, search boxes and footer."""
        out = _extract_as_configured(_REGISTER_PAGE, _no_selector())
        text = _text(out)
        for chrome in self.CHROME[:4]:
            assert chrome in text, chrome
