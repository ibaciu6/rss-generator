"""Tests for article-mode enrichment (scripts/enrichers/article_enricher.py).

Covers the per-item pipeline (fetch -> extract -> clean -> write back), the
`skipped` accounting that distinguishes a fetch failure from an empty
extraction, and the decision *not* to append a "Read more at source" trailer.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest

from scripts.enrichers import article_enricher as ae

ARTICLE_HTML = """<html><head>
<meta property="og:image" content="https://example.com/coperta.png">
</head><body><article><div class="articol-continut">
<p>Prima paragraf, cu suficiente caractere ca sa nu para un simplu excerpt
   scurt trimis de feed, ci corpul articolului insusi, pe cat mai lung.</p>
<p>Al doilea paragraf, continuarea textului articolului, cu alte cateva propozitii
   care adauga suficiente cuvinte incat pragul de continut real sa fie atins.</p>
</div></article></body></html>"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item>
  <title>Un articol</title>
  <link>https://example.com/un-articol</link>
  <guid>https://example.com/un-articol</guid>
  <description>Excerpt scurt din feed.</description>
</item>
</channel></rss>"""


def _feed(tmp_path: Path, items: int = 1) -> Path:
    body = "".join(
        f"<item><title>Articol {i}</title>"
        f"<link>https://example.com/articol-{i}</link>"
        f"<guid>https://example.com/articol-{i}</guid>"
        f"<description>Excerpt {i}.</description></item>"
        for i in range(items)
    )
    path = tmp_path / "blog.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>t</title>{body}</channel></rss>',
        encoding="utf-8",
    )
    return path


class _StubFetcher:
    """Stand-in for _fetch_article_page that serves a fixed page."""

    def __init__(self, html: str | None = ARTICLE_HTML):
        self.html = html
        self.urls: list[str] = []

    async def __call__(self, url, client=None, timeout=15.0):
        self.urls.append(url)
        return self.html


def _run(path: Path, fetcher, config: ae.ArticleEnrichConfig | None = None):
    original = ae._fetch_article_page
    ae._fetch_article_page = fetcher
    try:
        return asyncio.run(
            ae.enrich_article_feed(path, config=config or ae.ArticleEnrichConfig())
        )
    finally:
        ae._fetch_article_page = original


def _descriptions(path: Path) -> list[str]:
    channel = ET.parse(path).getroot().find("channel")
    return [item.findtext("description") or "" for item in channel.findall("item")]


class TestNoSourceTrailer:
    def test_no_read_more_link_is_appended(self, tmp_path):
        """The full body is already inline and every reader links the title to
        <link>, so the trailer was noise at the end of every article."""
        path = _feed(tmp_path)
        _run(path, _StubFetcher())
        assert "Read more at source" not in _descriptions(path)[0]

    def test_source_url_is_not_repeated_in_the_body(self, tmp_path):
        path = _feed(tmp_path)
        _run(path, _StubFetcher())
        body = _descriptions(path)[0]
        assert body.count("https://example.com/un-articol") == 0

    def test_body_is_actually_written(self, tmp_path):
        """Guards the opposite failure: dropping the trailer must not have
        dropped the article."""
        path = _feed(tmp_path)
        _run(path, _StubFetcher())
        body = _descriptions(path)[0]
        assert "Prima paragraf" in body
        assert "Excerpt scurt" not in body

    def test_reader_panel_has_no_source_link(self):
        """The panel is for reading, not for navigating away: no source link."""
        from scripts import local_reader as lr

        panel = lr.HTML_PAGE[lr.HTML_PAGE.index("function openPanel"):]
        panel = panel[: panel.index("\n}\n")]
        assert "it.link" not in panel
        assert "panel-source" not in panel


class TestPerItemPipeline:
    def test_one_fetch_per_item(self, tmp_path):
        path = _feed(tmp_path, items=3)
        fetcher = _StubFetcher()
        _run(path, fetcher)
        assert len(fetcher.urls) == 3

    def test_fetch_failure_counts_as_skipped_not_enriched(self, tmp_path):
        path = _feed(tmp_path)
        _, stats = _run(path, _StubFetcher(html=None))
        assert stats["skipped"] == 1
        assert stats["enriched"] == 0
        assert "Excerpt" in _descriptions(path)[0], "original description must survive"

    def test_enrichment_is_idempotent(self, tmp_path):
        """Running twice must not grow the body: the second pass re-extracts from
        the same source rather than appending to the enriched description."""
        path = _feed(tmp_path)
        _run(path, _StubFetcher())
        first = _descriptions(path)[0]
        _run(path, _StubFetcher())
        second = _descriptions(path)[0]
        assert first == second

    def test_featured_image_is_kept(self, tmp_path):
        path = _feed(tmp_path)
        _run(path, _StubFetcher())
        assert "https://example.com/coperta.png" in _descriptions(path)[0]

    def test_featured_image_can_be_disabled(self, tmp_path):
        path = _feed(tmp_path)
        _run(path, _StubFetcher(), ae.ArticleEnrichConfig(add_featured_image=False))
        assert "coperta.png" not in _descriptions(path)[0]

    def test_detail_article_selector_is_honoured(self, tmp_path):
        """Sites whose <article> wraps theme chrome need the narrow container."""
        body = (
            "Corpul real al articolului, scris in doua paragrafe suficient de "
            "lungi incat sa treaca pragul de continut si sa nu fie tratat ca "
            "o pagina goala; al doilea adaugand inca cateva propozitii despre "
            "ce a ramas in urma selectiei inguste."
        )
        assert ae.visible_text_length(body) >= ae.MIN_BODY_TEXT
        page = (
            '<html><body><article>'
            "<header><h1>Titlu</h1><p>byline</p></header>"
            "<nav>Distribuie articolul</nav>"
            f'<div class="articol-continut"><p>{body}</p></div>'
            "</article></body></html>"
        )
        path = _feed(tmp_path)
        _run(path, _StubFetcher(page), ae.ArticleEnrichConfig(content_selectors=[".articol-continut"]))
        desc = _descriptions(path)[0]
        assert "Corpul real" in desc
        assert "Titlu" not in desc
        assert "Distribuie" not in desc

    def test_empty_extraction_keeps_the_feed_excerpt(self, tmp_path):
        """The failure this guards: extract_main_content falls back to "clean the
        whole page", so an empty page yields a non-empty `<html>` shell. Writing
        that over a real excerpt is pure data loss."""
        path = _feed(tmp_path)
        _, stats = _run(path, _StubFetcher(html="<html><body></body></html>"))
        assert stats["enriched"] == 0
        assert stats["skipped"] == 1
        assert "Excerpt 0." in _descriptions(path)[0]
        assert "<html" not in _descriptions(path)[0]

    def test_near_empty_extraction_keeps_the_feed_excerpt(self, tmp_path):
        path = _feed(tmp_path)
        page = '<html><body><div class="paywall">Subscribe to continue reading</div></body></html>'
        _, stats = _run(path, _StubFetcher(page))
        assert stats["enriched"] == 0
        assert "Excerpt 0." in _descriptions(path)[0]

    def test_article_just_above_the_threshold_is_kept(self, tmp_path):
        """The guard keys on emptiness, not on article length: a modest but real
        post is content, and the feed's excerpt is no better than it."""
        body = "Scurt, dar real: " + ("un articol de o suta de caractere. " * 6)
        assert ae.MIN_BODY_TEXT <= ae.visible_text_length(body) < ae.MIN_BODY_TEXT * 2
        page = f'<html><body><article><p>{body}</p></article></body></html>'
        path = _feed(tmp_path)
        _, stats = _run(path, _StubFetcher(page))
        assert stats["enriched"] == 1
        assert "Scurt, dar real" in _descriptions(path)[0]


class TestFeaturedImageDedup:
    """The featured image is prepended to the body, so a WordPress article would
    otherwise render its lead photo twice: once as the prepended thumbnail and
    again at full size in the content."""

    def test_size_variant_of_an_in_content_image_is_not_duplicated(self):
        body = '<p>text</p><img src="https://x.ro/wp-content/uploads/2026/09/emag_points.jpg">'
        featured = "https://x.ro/wp-content/uploads/2026/09/emag_points-560x276.jpg"
        assert ae.body_contains_image(body, featured)

    def test_same_photo_at_a_different_size_is_not_duplicated(self):
        body = '<img src="https://x.ro/a-150x150.jpg">'
        assert ae.body_contains_image(body, "https://x.ro/a.jpg")

    def test_a_genuinely_new_featured_image_is_kept(self):
        body = '<img src="https://x.ro/other.jpg">'
        assert not ae.body_contains_image(body, "https://x.ro/lead-560x276.jpg")

    def test_no_images_in_the_body_means_the_featured_image_is_kept(self):
        assert not ae.body_contains_image("<p>only text</p>", "https://x.ro/lead.jpg")

    def test_unrelated_size_numbers_do_not_collide(self):
        """`a-150x150.jpg` and `a-560x276.jpg` are different photos only if the
        base names differ; the suffix must not be stripped so aggressively that
        distinct images match."""
        body = '<img src="https://x.ro/photo-150x150.jpg">'
        assert not ae.body_contains_image(body, "https://x.ro/other-150x150.jpg")

    def test_the_same_photo_behind_a_cdn_origin_prefix_is_not_duplicated(self):
        """WordPress image CDNs rewrite the origin host into the path:
        `i0.wp.com/nwradu.ro/wp-content/uploads/x.jpg?ssl=1` and
        `nwradu.ro/wp-content/uploads/x.jpg` are one photo. Comparing the paths
        verbatim left the featured image prepended to a body that already shows
        it, so the lead photo rendered twice -- reported on vasilescu and on
        securityaffairs (which serves `i0.wp.com/securityaffairs.com/...`)."""
        body = ('<img src="https://i0.wp.com/nwradu.ro/wp-content/uploads/2026/09/'
                'poza.jpg?ssl=1&amp;w=1600">')
        assert ae.body_contains_image(
            body, "https://nwradu.ro/wp-content/uploads/2026/09/poza.jpg"
        )

    def test_a_dotted_directory_is_not_mistaken_for_a_cdn_host(self):
        """The host heuristic is "labels then a trailing alphabetic TLD", which is
        what separates `site.com/` from a directory named `2026.09/`. A photo
        under such a directory must survive, or every image in the folder
        collapses onto one key and the article loses all but the first."""
        body = '<img src="https://x.ro/2026.09/poza.jpg">'
        assert not ae.body_contains_image(body, "https://x.ro/2026.09/alta.jpg")
        assert ae.body_contains_image(body, "https://x.ro/2026.09/poza.jpg")

    def test_a_nested_cdn_prefix_is_stripped_entirely(self):
        body = '<img src="https://i0.wp.com/cdn.example.co.uk/site.com/a/b.jpg">'
        assert ae.body_contains_image(body, "https://site.com/a/b.jpg")

    def test_end_to_end_duplicate_is_gone(self, tmp_path):
        """The hoinaru case: the body carries the full-size photo, so the
        prepended 560x276 thumbnail must be suppressed."""
        page = (
            '<html><head><meta property="og:image" content="https://x.ro/lead-560x276.jpg"></head>'
            '<body><article><div class="post_content">'
            "<p>Un articol cu o poza, scris suficient de lung incat sa treaca "
            "pragul de continut minim si sa nu fie tratat ca o pagina goala; "
            "al doilea paragraf adauga inca cateva propozitii pentru a depasi "
            "orice indoiala.</p>"
            '<img src="https://x.ro/lead.jpg">'
            "</div></article></body></html>"
        )
        path = _feed(tmp_path)
        _run(path, _StubFetcher(page))
        body = _descriptions(path)[0]
        assert body.count("https://x.ro/lead") == 1, body
        assert "lead-560x276" not in body


class TestEmbedSatisfiesMinimum:
    """An article that is mostly a video has little surrounding text but is
    still a real article. The presence of an embed must satisfy the
    minimum-content check on its own."""

    def test_video_article_is_kept(self, tmp_path):
        page = (
            '<html><head><meta property="og:image" content="https://x/ro/lead.jpg"></head>'
            '<body><article><div class="post_content">'
            "<p>Acum să vă văd. Pun pariu pe orice că nu știe nimeni.</p>"
            '<iframe src="https://www.facebook.com/plugins/video.php?height=476"></iframe>'
            "</div></article></body></html>"
        )
        path = _feed(tmp_path)
        _run(path, _StubFetcher(page))
        body = _descriptions(path)[0]
        assert "iframe" in body, "the video embed must survive"
        assert "Acum să vă văd" in body

    def test_empty_shell_is_still_rejected(self, tmp_path):
        page = '<html><body><article><div class="post_content"></div></article></body></html>'
        path = _feed(tmp_path)
        _, stats = _run(path, _StubFetcher(page))
        assert stats["enriched"] == 0


class TestChromeOnlyIsNotAnArticle:
    """naked-security's page is a client-side-rendering bailout: the prose is not
    in the markup at all, so extraction returned the site navigation -- 2,417
    visible characters, well over MIN_BODY_TEXT, published as the article body on
    all 9 items and reported by the run as `rich=9`. Length cannot tell a menu
    from an article, so it needs its own gate."""

    @staticmethod
    def _nav():
        """The shape of the published fragment: a tracking iframe, a skip link,
        client-side-rendering templates, then the whole `<header>`."""
        return (
            '<iframe height="0" src="https://www.googletagmanager.com/ns.html" '
            'style="display:none" width="0"></iframe>'
            '<a class="sr-only focus:not-sr-only" href="#main-content">Skip to Content</a>'
            '<!--$!--><template data-dgst="BAILOUT_TO_CLIENT_SIDE_RENDERING"></template>'
            '<!--/$-->'
            '<header class="site-header sticky flex top-0">'
            '<div class="flex flex-row flex-wrap items-center justify-between">'
            '<ul class="main-menu flex flex-row">'
            '<li class="li"><a href="/en-us/trust"><div class="line-clamp-2">'
            "Trust center</div></a></li>"
            '<li class="li"><a href="/en-us/products"><div class="line-clamp-2">'
            "Explore products</div></a></li>"
            "</ul></div></header>"
        )

    def test_it_is_long_enough_to_have_slipped_through(self):
        assert ae.visible_text_length(self._nav()) >= ae.MIN_BODY_TEXT

    def test_a_whole_page_navigation_is_recognised(self):
        assert ae.is_chrome_only(self._nav())

    def test_the_item_keeps_its_excerpt_instead_of_the_menu(self, tmp_path):
        path = _feed(tmp_path)
        _, stats = _run(path, _StubFetcher(f"<html><body>{self._nav()}</body></html>"))
        assert stats["enriched"] == 0
        assert stats["kept_excerpt"] == 1
        assert stats["chrome_only"] == 1, stats
        assert "main-menu" not in _descriptions(path)[0]

    def test_a_tracking_pixel_is_not_content(self):
        """The fragment's first element is an analytics frame: hidden, and sized
        0x0. It must not be weighed as content, or the menu passes. A frame that
        does draw something is weighed -- what it shows is not knowable from the
        markup, and ``visible_text_length`` already gives embeds the benefit of
        the doubt so a video article is not judged empty."""
        nav = self._nav()
        assert 'height="0"' in nav and "display:none" in nav
        assert ae.is_chrome_only(nav)
        shown = nav.replace(
            '<iframe height="0" src="https://www.googletagmanager.com/ns.html" '
            'style="display:none" width="0"',
            '<iframe height="250" width="300" '
            'src="https://www.googletagmanager.com/ns.html"',
        )
        assert shown != nav, "the fixture must have changed for this test to mean anything"
        assert not ae.is_chrome_only(shown)

    def test_a_zero_sized_frame_is_not_content_either(self):
        """Sized 0x0 but not styled away -- a frame that draws no pixels is not an
        article, and a visible-frame test alone would miss it."""
        nav = self._nav().replace(' style="display:none"', "")
        assert ae.is_chrome_only(nav)

    def test_an_article_beside_a_menu_is_not_thrown_away(self):
        nav = self._nav()
        prose = "<div><p>" + ("The actual report runs to several paragraphs. " * 20) + "</p></div>"
        assert not ae.is_chrome_only(nav + prose)

    def test_prose_without_a_paragraph_tag_is_still_an_article(self):
        """Some themes write a body of bare divs and spans. Judging by prose tags
        alone would discard them, so the text floor is the second condition."""
        long_div = "<div>" + ("words that make up a real sentence here. " * 20) + "</div>"
        assert not ae.is_chrome_only("<header><nav>Home About</nav></header>" + long_div)

    def test_a_list_long_enough_to_be_an_article_is_not_chrome(self):
        facts = "".join(f"<li>Fact number {n} and its explanation.</li>" for n in range(12))
        assert not ae.is_chrome_only(f"<nav>Home About</nav><ul>{facts}</ul>")

    def test_a_stub_below_the_floor_keeps_the_excerpt_instead(self):
        """38 characters is not an article by this project's own measure. The
        rule that saves a real list -- it has prose and length -- saves this
        fragment's reader too, because the site shipped an excerpt of it."""
        assert ae.is_chrome_only('<nav>Home About</nav><ul><li>A fact</li><li>Another</li></ul>')

    def test_a_photo_article_is_kept_for_its_picture(self):
        """No prose at all, so the text floor alone would discard it -- but for a
        photograph the image is the item."""
        assert not ae.is_chrome_only(
            "<header><nav>Menu</nav></header><img src='/photo-of-the-flood.jpg'>"
        )

    def test_a_video_article_is_kept_for_its_embed(self):
        assert not ae.is_chrome_only(
            "<header><nav>Menu</nav></header>"
            '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
        )

    def test_an_empty_string_is_not_called_chrome(self):
        """The length gate already owns emptiness; saying so twice would make the
        two counters indistinguishable."""
        assert not ae.is_chrome_only("")
        assert not ae.is_chrome_only("   ")


class TestLooksLikeChallenge:
    """A Cloudflare interstitial is a 200-OK response that is not the article.
    It carries a spinner, keyframes and real sentences, so it passes
    MIN_BODY_TEXT and would overwrite a good RSS excerpt."""

    def test_detects_the_hoidaru_interstitial(self):
        page = ("<html><head><title>One moment, please...</title></head><body>"
                "<div class='spinner'></div><p>Please wait while your request "
                "is being verified...</p></body></html>")
        assert ae.looks_like_challenge(page)

    @pytest.mark.parametrize("page", [
        "<html><head><title>Just a moment...</title></head><body>Checking your "
        "browser before accessing example.com</body></html>",
        "<html><body>Enable JavaScript and cookies to continue</body></html>",
        "<html><body>DDoS protection by Cloudflare</body></html>",
        "<html><body>Attention Required! | Cloudflare</body></html>",
        "<html><body>You have been blocked</body></html>",
        "<html><body>Ray ID: 8a3f2b1c9d4e</body></html>",
    ])
    def test_detects_common_interstitials(self, page):
        assert ae.looks_like_challenge(page)

    @pytest.mark.parametrize("body", [
        "New variant of an old scam: Use the framing of a CAPTCHA to get an "
        "unsuspecting user to download and run a malicious program.",
        "Attackers hijacked Ukrainian websites to deliver a fake Cloudflare "
        "CAPTCHA that installs a stealer.",
        "Stage 2: the loader drops the payload into memory.",
        "Attention Required! Our web server is under maintenance.",
    ])
    def test_keeps_real_articles(self, body):
        """Security feeds publish articles about CAPTCHAs, Cloudflare and
        malware "loaders" -- a naive keyword test would destroy 11 good items."""
        assert not ae.looks_like_challenge(f"<article><p>{body}</p></article>")

    def test_empty_is_not_a_challenge(self):
        assert not ae.looks_like_challenge("")


class TestChallengeIsNotWritten:
    @pytest.mark.asyncio
    async def test_interstitial_keeps_the_existing_description(self, tmp_path):
        """The end-to-end behaviour: a challenged fetch must leave the feed
        item alone rather than storing the challenge page."""
        path = tmp_path / "site.xml"
        path.write_text(
            '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            "<item><title>Articol bun</title><link>https://x.ro/articol</link>"
            "<description>Rezumatul original, bun, din feed.</description>"
            "</item></channel></rss>",
            encoding="utf-8",
        )
        challenge = (
            "<html><head><title>One moment, please...</title></head><body>"
            "<div class='spinner'></div><p>Please wait while your request is "
            "being verified</p></body></html>"
        )

        async def fake_fetch(url, client=None, timeout=None):
            return challenge

        original = ae._fetch_article_page
        ae._fetch_article_page = fake_fetch
        try:
            changed, stats = await ae.enrich_article_feed(
                path,
                config=ae.ArticleEnrichConfig(),
            )
        finally:
            ae._fetch_article_page = original

        assert stats["skipped"] == 1
        assert stats["enriched"] == 0
        assert changed is False
        assert "Rezumatul original" in path.read_text(encoding="utf-8")
        assert "One moment" not in path.read_text(encoding="utf-8")


class TestKeptExcerptIsCounted:
    """An item that keeps only the site's own RSS excerpt is a stub in the
    published feed, and it used to be invisible: it was folded into `skipped`,
    which is never surfaced, so a feed could report a successful enrich while
    every single item stayed a snippet.
    """

    @staticmethod
    def _stats():
        from scripts.enrichers.article_enricher import ArticleEnrichConfig
        return ArticleEnrichConfig()

    def test_the_stat_keys_exist_from_the_start(self):
        """A missing key would raise KeyError at the point of accumulation, in
        the middle of a run over every feed."""
        import asyncio

        from scripts.enrichers.article_enricher import enrich_article_feed

        feed = tmp = None
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "f.xml"
            tmp.write_text(
                '<?xml version="1.0"?><rss><channel><title>t</title>'
                "<item><title>a</title></item></channel></rss>",
                encoding="utf-8",
            )
            _changed, stats = asyncio.run(
                enrich_article_feed(tmp, config=self._stats())
            )
        for key in ("items", "enriched", "skipped", "kept_excerpt", "fetch_failed", "challenge"):
            assert key in stats, f"{key} missing from the stats dict"
        assert stats["kept_excerpt"] == 0
        del feed

    def test_an_item_with_no_link_is_not_counted_as_kept_excerpt(self):
        """No link is a data problem, not a truncated article; conflating them
        would make the headline number meaningless."""
        import asyncio
        import tempfile
        from pathlib import Path

        from scripts.enrichers.article_enricher import enrich_article_feed

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "f.xml"
            f.write_text(
                '<?xml version="1.0"?><rss><channel><title>t</title>'
                "<item><title>no link here</title>"
                "<description>excerpt</description></item></channel></rss>",
                encoding="utf-8",
            )
            _changed, stats = asyncio.run(enrich_article_feed(f, config=self._stats()))
        assert stats["skipped"] == 1
        assert stats["kept_excerpt"] == 0


class TestChallengeDetection:
    """A challenge page that slips through is published as the article body.

    The Register's interstitial matched none of the Cloudflare/JS patterns the
    detector was built from, so 25 of its 30 items shipped as 1.2 KB of robot
    check -- the single worst thing a feed can contain, and invisible in every
    count the pipeline reports, because the markup *was* the description.
    """

    WICKETKEEPER = (
        '<!DOCTYPE html><html><head><title>Are we human?</title></head>'
        '<body><div class="wicketkeeper" data-callback="solved" '
        'data-input-name="solution" style="margin:20vh auto 0 auto;"></div>'
        "</body></html>"
    )

    def test_the_wicketkeeper_interstitial_is_a_challenge(self):
        assert ae.looks_like_challenge(self.WICKETKEEPER)

    def test_the_title_alone_is_enough(self):
        assert ae.looks_like_challenge("<html><head><title>Are we human?</title></head><body></body></html>")

    def test_a_real_article_is_not_a_challenge(self):
        assert not ae.looks_like_challenge(
            "<html><head><title>Are we human? | The Register</title></head>"
            "<body><article><p>Six hundred words of ordinary prose.</p></article></body></html>"
        )

    def test_a_body_quoting_the_word_is_not_a_challenge(self):
        """The phrase appears in articles *about* bot walls, so the marker is
        matched on the interstitial's markup, not on the words alone."""
        assert not ae.looks_like_challenge(
            "<article><p>Cloudflare asks visitors whether they are human.</p></article>"
        )


class TestAFullArticleIsNeverDiscardedForAHairOverTheCap:
    """The cap used to be a gate rather than a bound.

    `remove_ads_and_boilerplate` and the removal modules both *add* bytes as
    well as removing them, so a body already truncated to MAX_DESCRIPTION_LENGTH
    could finish back over it -- and the final check then refused to publish it
    at all. buletin-de-bucuresti measured 50,021 bytes carrying 48,493
    characters of article and shipped all ten items as their site excerpt, with
    nothing in the logs but a bare "skipped".

    The cap exists to keep an item a sane size, which truncation satisfies.
    Losing a whole article for being 21 bytes over does not.
    """

    def test_a_body_just_over_the_cap_is_published_truncated(self, tmp_path, monkeypatch):
        feed = _feed(tmp_path)
        prose = "<p>" + ("word " * 30_000) + "</p>"
        page = "<html><body><article>" + prose + "</article></body></html>"
        # A removal step that leaves the body a handful of bytes over the cap,
        # which is what the real ad remover and the removal modules do.
        original = ae.remove_ads_and_boilerplate
        monkeypatch.setattr(
            ae, "remove_ads_and_boilerplate", lambda html, **kw: original(html, **kw) + " " * 64
        )

        _run(feed, _StubFetcher(page))

        body = _descriptions(feed)[0]
        assert body, "the article was discarded for being over the cap"
        assert len(body) <= ae.MAX_DESCRIPTION_LENGTH, len(body)
        assert ae.visible_text_length(body) >= ae.MIN_BODY_TEXT
        assert "Excerpt 0." not in body, "the excerpt should have been replaced"

    def test_the_cap_still_bounds_the_result(self, tmp_path, monkeypatch):
        """Truncating must not become 'keep everything'."""
        feed = _feed(tmp_path)
        page = "<html><body><article><p>" + ("word " * 60_000) + "</p></article></body></html>"
        _run(feed, _StubFetcher(page))
        body = _descriptions(feed)[0]
        assert len(body) <= ae.MAX_DESCRIPTION_LENGTH, len(body)


class TestFetchingWithoutASharedClient:
    """`_fetch_article_page` awaited `httpx.get`, which is synchronous.

    `await` on a Response raises TypeError, and the `except Exception` around
    it turned that into "fetch failed" -- so every call without a shared client
    returned None and reported a network error that never happened. The enrich
    loop always passes a client, so production never showed it; a caller that
    does not gets nothing and is told the network is at fault.
    """

    def test_it_fetches_when_no_client_is_supplied(self, monkeypatch):
        class _Resp:
            status_code = 200
            text = "<html><body><article>the article</article></body></html>"

        class _OneShot:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                return _Resp()

        seen = {}

        def _client(**kw):
            seen.update(kw)
            return _OneShot()

        monkeypatch.setattr(ae.httpx, "AsyncClient", _client)
        out = asyncio.run(ae._fetch_article_page("https://example.com/a"))
        assert out is not None and "the article" in out
        assert seen.get("follow_redirects") is True


class TestCapOutput:
    """`truncate_content` bounds visible text; the cap the reader sees is on the
    string. Enforcing the second one by refusing to publish is what lost
    buletin-de-bucuresti's ten articles."""

    def test_a_string_within_the_cap_is_untouched(self):
        body = "<p>short</p>"
        assert ae._cap_output(body, 100) == body

    def test_it_cuts_to_the_cap(self):
        assert len(ae._cap_output("x" * 500, 100)) <= 100

    @pytest.mark.parametrize("cap", [110, 137, 250, 400])
    def test_it_never_ends_mid_tag(self, cap):
        body = "<p>" + "y" * 900 + "</p>" + "<b class=\"x\">tail</b>"
        out = ae._cap_output(body, cap)
        assert len(out) <= cap
        # A half-written tag is the thing to rule out: it swallows the rest of
        # the item's markup in the reader.
        tail = out[out.rfind("<") + 1 :]
        assert tail == "" or ">" in tail or "<" in tail, repr(out[-30:])

    def test_a_run_of_markup_does_not_cut_the_prose_away(self):
        """Retreating to the last '<' must not be able to gut the body when the
        final stretch is all tags."""
        html = "<p>" + "z" * 80 + "</p>" + "<span>" * 40
        out = ae._cap_output(html, 100)
        assert len(out) <= 100
        assert "z" * 80 in out


class TestTheCapBindsTheDescriptionNotTheBody:
    """The featured image and any retained excerpt are added *after* the body
    was capped, and they put it back over: buletin-de-bucuresti measured 50,253
    bytes against a 50,000 cap with a 253-byte featured image on the front.
    Capping the body and then declaring the item in-bounds measures the wrong
    string -- the one the reader loads is the assembled description."""

    def test_the_assembled_description_respects_the_cap(self, tmp_path, monkeypatch):
        feed = _feed(tmp_path)
        page = "<html><body><article><p>" + ("word " * 60_000) + "</p></article></body></html>"
        monkeypatch.setattr(ae, "extract_featured_image", lambda html: "https://img.example/x.jpg")
        _run(feed, _StubFetcher(page))
        for body in _descriptions(feed):
            assert len(body) <= ae.MAX_DESCRIPTION_LENGTH, len(body)

    def test_the_featured_image_survives_the_cut(self, tmp_path, monkeypatch):
        """Cutting the tail must not take the leading image with it."""
        feed = _feed(tmp_path)
        page = "<html><body><article><p>" + ("word " * 60_000) + "</p></article></body></html>"
        monkeypatch.setattr(ae, "extract_featured_image", lambda html: "https://img.example/x.jpg")
        _run(feed, _StubFetcher(page))
        assert "https://img.example/x.jpg" in _descriptions(feed)[0]


class TestPageFetchFailuresSayWhy:
    """A 403 and a refused connection are different diagnoses, and both arrive
    as the same `None`.

    100 items came out of one run as `ON-EXCERPT=100(fetch 100)` -- one figure
    covering 403s, timeouts, protocol errors and refused connections, with
    nothing to tell them apart. That is the same blindness as a silent API
    fallback, and it is why the proxy search and the WordPress attempt were
    both partly guesswork: the evidence needed to rule them out was not in the
    log.
    """

    class _C:
        def __init__(self, *, status=200, raises=None, text="<html>body</html>"):
            self.status, self.raises, self.text = status, raises, text

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            if self.raises:
                raise self.raises
            return type("R", (), {"status_code": self.status, "text": self.text})()

    def test_a_200_returns_the_body(self, monkeypatch):
        c = self._C()
        monkeypatch.setattr(ae.httpx, "AsyncClient", lambda **kw: c)
        assert "body" in asyncio.run(ae._fetch_article_page("https://x.test/a"))

    def test_a_403_is_logged_with_its_status(self, monkeypatch, caplog):
        import structlog

        c = self._C(status=403, text="blocked")
        monkeypatch.setattr(ae.httpx, "AsyncClient", lambda **kw: c)
        with structlog.testing.capture_logs() as logs:
            assert asyncio.run(ae._fetch_article_page("https://x.test/a")) is None
        ev = [e for e in logs if e.get("event") == "enrich.fetch_status"]
        assert ev and ev[0]["status"] == 403, logs

    @pytest.mark.parametrize(
        "exc,fragment",
        [
            (httpx.ConnectTimeout("timed out"), "connect timeout"),
            (httpx.ReadTimeout("slow"), "read timeout"),
            (httpx.ConnectError("refused"), "connect error"),
            (httpx.RemoteProtocolError("bad frame"), "protocol error"),
        ],
    )
    def test_transport_failures_are_named_distinctly(self, monkeypatch, exc, fragment):

        assert fragment in ae._reason(exc)
        c = self._C(raises=exc)
        monkeypatch.setattr(ae.httpx, "AsyncClient", lambda **kw: c)
        import structlog

        with structlog.testing.capture_logs() as logs:
            assert asyncio.run(ae._fetch_article_page("https://x.test/a")) is None
        ev = [e for e in logs if e.get("event") == "enrich.fetch_failed"]
        assert ev and fragment in ev[0]["reason"], logs
