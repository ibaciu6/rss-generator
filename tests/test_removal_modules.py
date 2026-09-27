"""Tests for the reusable removal modules (scripts/enrichers/removal_modules.py).

Each module removes one category of chrome. These tests pin the behaviour the
scan found in the wild, so a module that stops working fails here rather than
silently shipping chrome to a feed.
"""
from __future__ import annotations

from scripts.enrichers.removal_modules import apply_modules, known_modules


def _text(html: str) -> str:
    from bs4 import BeautifulSoup

    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())


def _apply(html: str, *modules: str) -> str:
    return apply_modules(html, list(modules))


def test_registry_is_populated():
    """Every module the scan justified must exist."""
    expected = {"comments", "akismet-notice", "sponsor-block", "emoji-images",
                "related-posts", "social-share", "head-meta", "the-tags"}
    assert expected <= set(known_modules()), sorted(known_modules())


class TestComments:
    def test_removes_comment_section_by_class(self):
        html = "<p>articol</p><div class='comments-area'>60 comentarii</div>"
        assert "comentarii" not in _text(_apply(html, "comments"))

    def test_removes_comment_form(self):
        html = "<p>articol</p><div class='comment-respond'>Adaugă comentariu Anulează</div>"
        assert "Adaugă comentariu" not in _text(_apply(html, "comments"))

    def test_removes_comenteaza_links(self):
        # In the real theme "Comenează" sits inside the post-header div, so the
        # module finds a block ancestor to remove.
        html = "<div class='post-header'><a href='#'>Comenează</a></div><p>articol</p>"
        assert "Comenează" not in _text(_apply(html, "comments"))

    def test_keeps_article_text(self):
        html = "<p>Articolul propriu-zis, cu continut real.</p><div class='comment-respond'>form</div>"
        assert "continut real" in _text(_apply(html, "comments"))


class TestAkismetNotice:
    def test_removes_by_class(self):
        html = "<p>articol</p><p class='akismet_comment_form_privacy_notice'>Acest site folosește Akismet</p>"
        assert "Akismet" not in _apply(html, "akismet-notice")

    def test_removes_by_text(self):
        html = "<p>articol</p><p>Acest site folosește Akismet pentru a reduce spamul.</p>"
        assert "Akismet" not in _apply(html, "akismet-notice")


class TestSponsorBlock:
    def test_removes_eurocharge_block(self):
        """The real mihai-vasilescu case: Tailwind-hashed classes, stable text."""
        html = (
            "<p>articol</p>"
            "<div class='x14z9mp xat24cr x1lziwak x1vvkbs xtlvy1s x126k92a'>"
            "<span>EUROCHARGE</span> by Schaeffler</div>"
            "<div class='x14z9mp xat24cr x1lziwak x1vvkbs xtlvy1s x126k92a'>"
            "Parteneri: BT Leasing Banca Transilvania ENGIE VARTA Automotive Michelin</div>"
            "<div class='x14z9mp xat24cr x1lziwak x1vvkbs xtlvy1s x126k92a'>Eldrive Romania</div>"
        )
        out = _text(_apply(html, "sponsor-block"))
        assert "EUROCHARGE" not in out
        assert "Parteneri" not in out
        assert "Eldrive" not in out
        assert "articol" in out

    def test_keeps_unrelated_text(self):
        html = "<p>articol despre romanii din Spania</p>"
        assert "Spania" in _text(_apply(html, "sponsor-block"))


class TestEmojiImages:
    def test_removes_fbcdn_emoji(self):
        html = '<p>articol</p><img src="https://static.xx.fbcdn.net/images/emoji.php/v9/t4c/1/16/25aa.png">'
        assert "fbcdn" not in _apply(html, "emoji-images")

    def test_keeps_real_images(self):
        html = '<p>articol</p><img src="https://www.manafu.ro/wp-content/uploads/2026/09/photo.jpg">'
        assert "photo.jpg" in _apply(html, "emoji-images")


class TestRelatedPosts:
    def test_removes_crp_related(self):
        html = '<p>articol</p><div class="crp_related crp-text-only">Articole similare: X</div>'
        assert "Articole similare" not in _text(_apply(html, "related-posts"))

    def test_removes_by_text(self):
        html = '<p>articol</p><div>Pe aceeaşi temă: Alt articol</div>'
        assert "aceeaşi temă" not in _text(_apply(html, "related-posts"))


class TestSocialShare:
    def test_removes_share_row(self):
        html = '<p>articol</p><div class="post_share_new">Facebook Twitter Whatsapp</div>'
        assert "Facebook" not in _text(_apply(html, "social-share"))

    def test_removes_gabriel_ursan_share(self):
        html = '<nav class="partajare coloana">Distribuie articolul</nav><p>articol</p>'
        assert "Distribuie" not in _text(_apply(html, "social-share"))


class TestHeadMeta:
    def test_removes_meta_and_noscript(self):
        html = '<p>articol</p><meta name="x"><noscript>Enable JavaScript and cookies</noscript>'
        out = _apply(html, "head-meta")
        assert "<meta" not in out
        assert "Enable JavaScript" not in out
        assert "articol" in out


class TestTheTags:
    def test_removes_tags_footer(self):
        html = '<p>articol</p><div class="the-tags">Romania, Date Personale</div>'
        assert "Date Personale" not in _text(_apply(html, "the-tags"))


class TestComposition:
    def test_modules_apply_in_order(self):
        html = (
            '<p>articol</p>'
            '<div class="comments-area">comentarii</div>'
            '<p class="akismet_comment_form_privacy_notice">Akismet</p>'
            '<img src="https://static.xx.fbcdn.net/x.png">'
        )
        out = _apply(html, "comments", "akismet-notice", "emoji-images")
        text = _text(out)
        assert "comentarii" not in text
        assert "Akismet" not in text
        assert "fbcdn" not in text
        assert "articol" in text

    def test_empty_module_list_is_a_noop(self):
        html = "<p>articol</p>"
        assert _apply(html) == html

    def test_unknown_module_is_ignored(self):
        html = "<p>articol</p>"
        assert _apply(html, "no-such-module") == html
