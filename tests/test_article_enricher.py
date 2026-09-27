"""Tests for article-mode enrichment (scripts/enrichers/article_enricher.py).

Covers the per-item pipeline (fetch -> extract -> clean -> write back), the
`skipped` accounting that distinguishes a fetch failure from an empty
extraction, and the decision *not* to append a "Read more at source" trailer.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

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
