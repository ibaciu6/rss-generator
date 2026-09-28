"""Tests for the reusable removal modules (scripts/enrichers/removal_modules.py).

Each module removes one category of chrome. These tests pin the behaviour the
scan found in the wild, so a module that stops working fails here rather than
silently shipping chrome to a feed.
"""
from __future__ import annotations

import pytest

from scripts.enrichers import article_enricher as ae
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

    def test_removes_emoji_in_data_src(self):
        """Lazy-loaded emoji use data-src instead of src."""
        html = '<p>articol</p><img data-src="https://static.xx.fbcdn.net/x/emoji.png">'
        assert "fbcdn" not in _apply(html, "emoji-images")


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

    def test_unwraps_lazyload_noscript_and_keeps_the_photo(self):
        """A lazy-loading theme wraps the real photo in <noscript>; deleting
        it would take the article's only image with it (darknet-the-darkside)."""
        html = ('<p>articol</p><figure class="wp-block-image">'
                '<noscript><img src="https://www.darknet.org.uk/wp-content/'
                'uploads/2026/09/shot.jpg" alt="Captura"></noscript></figure>')
        out = _apply(html, "head-meta")
        assert "<noscript" not in out
        assert "shot.jpg" in out, out
        assert "articol" in out

    def test_drops_text_only_noscript_interstitial(self):
        html = '<p>articol</p><noscript>Enable JavaScript and cookies to continue</noscript>'
        out = _apply(html, "head-meta")
        assert "<noscript" not in out
        assert "Enable JavaScript" not in out

    def test_removes_script_and_style(self):
        html = '<p>articol</p><script>alert(1)</script><style>body{}</style>'
        out = _apply(html, "head-meta")
        assert "<script" not in out
        assert "<style" not in out
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


class TestPostNavigation:
    def test_removes_next_article_nav(self):
        html = (
            "<p>articol</p>"
            "<nav class='post-navigation is-width-constrained'>"
            "<a href='#'>Următor Articol</a></nav>"
        )
        out = _apply(html, "post-navigation")
        assert "Următor Articol" not in _text(out)
        assert "articol" in _text(out)


class TestPromoFooter:
    def test_removes_emag_offer_block(self):
        html = (
            "<p>articol</p>"
            "<div class='rb-emag-offer__content'>"
            "<div class='rb-emag-offer__textwrap'>"
            "Oferta zilei la eMAG din data 27-09-2026"
            "</div></div>"
        )
        out = _apply(html, "promo-footer")
        assert "Oferta zilei" not in _text(out)

    def test_removes_offer_rendered_as_aside(self):
        """The live site ships <aside class="rb-emag-offer">; a `div.`-prefixed
        selector missed it and the offer came back in the published feed."""
        html = ('<p>articol</p><aside aria-label="Oferta zilei la eMAG" '
                'class="rb-emag-offer rb-emag-offer--article">'
                '<div class="rb-emag-offer__inner">'
                '<a class="rb-emag-offer__img" href="https://e.emag.com/x">Oferta zilei la eMAG</a>'
                "</div></aside>")
        out = _apply(html, "promo-footer")
        assert "Oferta zilei" not in _text(out)
        assert "rb-emag-offer" not in out
        assert "articol" in _text(out)

    def test_nested_bem_children_do_not_break(self):
        html = ('<p>articol</p><aside class="rb-emag-offer">'
                '<div class="rb-emag-offer__inner">'
                '<div class="rb-emag-offer__textwrap">Oferta zilei</div>'
                "</div></aside>")
        out = _apply(html, "promo-footer")
        assert "rb-emag-offer" not in out
        assert "articol" in _text(out)

    def test_removes_google_news_banner(self):
        html = "<p>articol</p><div>Adaugă revoblog ca sursă preferată în Google News</div>"
        out = _apply(html, "promo-footer")
        assert "sursă preferată" not in _text(out)


class TestVisibleTextLengthEmbeds:
    """An iframe/video embed counts as content: an article that is mostly a
    video has little surrounding text but is still a real article."""

    def test_embed_counts_as_a_full_body(self):
        """A video-first article has little text but is real content, so an
        embed is worth a full MIN_BODY_TEXT rather than one character."""
        assert ae.visible_text_length('<iframe src="https://x.com/v"></iframe>') == ae.MIN_BODY_TEXT

    def test_text_plus_embed(self):
        html = "<p>un articol scurt.</p><iframe src='https://x.com/v'></iframe>"
        assert ae.visible_text_length(html) == 17 + ae.MIN_BODY_TEXT

    def test_empty_has_no_embeds(self):
        assert ae.visible_text_length("") == 0

    def test_text_only_is_not_padded(self):
        """Without an embed the score is the plain text length, so a short
        page is still rejected."""
        assert ae.visible_text_length("<p>hi</p>") == 2


class TestAuthorBox:
    """The author bio + "Articole: N" block repeats verbatim on every post, so
    it reads as boilerplate rather than content."""

    def test_removes_author_box(self):
        html = (
            "<p>articol</p>"
            "<div class='author-box is-width-constrained'>"
            "<a class='ct-media-container'><img src='/author.jpg'></a>"
            "<section>Printesa Urbana Scriu de cind ma stiu.</section>"
            "</div>"
        )
        out = _apply(html, "author-box")
        assert "Printesa Urbana" not in _text(out)
        assert "author.jpg" not in out
        assert "articol" in _text(out)

    def test_removes_author_bio_inner_div(self):
        html = "<p>articol</p><div class='author-box-bio'>Bio aici</div>"
        assert "Bio aici" not in _apply(html, "author-box")


class TestThemeIcons:
    def test_removes_theme_ui_icon(self):
        html = ("<p>articol</p>"
                '<img src="https://snoop.ro/wp-content/themes/snoop/public/images/icon-google.d418db.svg">')
        assert "icon-google" not in _apply(html, "theme-icons")

    def test_removes_menu_icon(self):
        html = ('<p>articol</p><img src="https://securityaffairs.com'
                '/wp-content/themes/security_affairs/images/menu-icon.svg">')
        assert "menu-icon" not in _apply(html, "theme-icons")

    def test_keeps_content_photo_from_uploads(self):
        html = ('<p>articol</p><img src="https://x.ro/wp-content/uploads/2026/09/photo.jpg">')
        assert "photo.jpg" in _apply(html, "theme-icons")


class TestDedupeImages:
    def test_removes_size_variant_duplicate(self):
        html = ('<p>articol</p>'
                '<img src="https://x.ro/uploads/anti-slapp.png">'
                '<img src="https://x.ro/uploads/anti-slapp-845x321.png">')
        out = _apply(html, "dedupe-images")
        assert out.count("<img") == 1, out

    def test_keeps_distinct_photos(self):
        html = ('<p>articol</p>'
                '<img src="https://x.ro/uploads/one.jpg">'
                '<img src="https://x.ro/uploads/two.jpg">')
        assert _apply(html, "dedupe-images").count("<img") == 2

    def test_keeps_first_occurrence(self):
        html = ('<img src="https://x.ro/uploads/p.jpg"><img src="https://x.ro/uploads/p-100x100.jpg">')
        out = _apply(html, "dedupe-images")
        assert "p.jpg" in out and "p-100x100" not in out


class TestThemeAssetPathRule:
    """The name-based heuristic missed user.svg, calendar.svg, rss.png and
    avatar_default_*.png. The path is the reliable discriminator."""

    @pytest.mark.parametrize("src", [
        "https://www.digitalcitizen.ro/wp-content/themes/digcit-aprilie-2026/images/user.svg",
        "https://www.digitalcitizen.ro/wp-content/themes/digcit-aprilie-2026/images/calendar.svg",
        "https://www.schneier.com/wp-content/themes/schneier/assets/images/rss.png",
        "https://securelist.com/wp-content/themes/securelist2020/assets/images/avatar-default/avatar_default_1.png",
        "https://securityaffairs.com/wp-content/themes/security_affairs/images/resecurity_banner_header_mobile.png",
    ])
    def test_removes_any_theme_asset(self, src):
        html = f'<p>articol</p><img src="{src}">'
        assert "<img" not in _apply(html, "theme-icons")

    def test_keeps_uploaded_photo(self):
        html = '<p>articol</p><img src="https://x.ro/wp-content/uploads/2026/09/photo.jpg">'
        assert "photo.jpg" in _apply(html, "theme-icons")


class TestPageShell:
    def test_removes_link_and_title(self):
        html = ('<p>articol</p><link rel="icon" href="/favicon.ico">'
                "<head><title>Site</title></head>")
        out = _apply(html, "page-shell")
        assert "<link" not in out and "favicon" not in out

    def test_removes_doctype(self):
        html = '<!DOCTYPE html>\n<html><body><p>articol</p></body></html>'
        assert "DOCTYPE" not in _apply(html, "page-shell")

    def test_unwraps_body_and_keeps_the_article(self):
        """Regression guard: decomposing <body> deleted the entire article and
        made every item look empty, so all 19 were skipped."""
        html = '<!DOCTYPE html><html><head><title>T</title></head><body><p>articol</p></body></html>'
        out = _apply(html, "page-shell")
        assert "articol" in _text(out), out
        assert "<body" not in out and "<html" not in out and "<head" not in out


class TestSvgSprites:
    def test_removes_theme_sprite_svg(self):
        html = ('<p>articol</p><svg class="o-icon"><use '
                'xlink:href="https://x.ro/wp-content/themes/t/assets/sprite/icons.svg#i"></use></svg>')
        assert "<svg" not in _apply(html, "svg-sprites")

    def test_keeps_article_svg(self):
        html = '<p>articol</p><svg viewBox="0 0 10 10"><path d="M0 0"/></svg>'
        assert "<svg" in _apply(html, "svg-sprites")


class TestGnewsBanner:
    def test_removes_edupedu_banner(self):
        html = ('<p>articol</p><div class="edupedu-google-wrap">'
                '<a class="edupedu-google-button">'
                '<span class="edupedu-google-text">Adaugă-ne ca sursă preferată în Google</span>'
                "</a></div>")
        assert "preferată" not in _apply(html, "gnews-banner")

    def test_removes_by_text_when_class_differs(self):
        html = '<p>articol</p><div>Adaugă-ne ca sursă preferată în Google News</div>'
        assert "preferată" not in _apply(html, "gnews-banner")


class TestSubscribeForms:
    """A <form> in article prose is never the article. Four different sites
    shipped a signup/search box that rendered as a visible input in the reader."""

    def test_removes_mailchimp_block_and_its_comment(self):
        html = ("<p>articol</p>\n<!-- Begin MailChimp Signup Form -->\n"
                '<div id="mc_embed_signup"><form action="//x.us3.list-manage.com/'
                'subscribe/post?u=1&id=2" method="post" target="_blank">'
                '<label for="mce-EMAIL">Ți-a plăcut articolul? Abonează-te și vei '
                "primi un mail cu rezumatul articolelor scrise recent pe blog</label>"
                '<input class="email" id="mce-EMAIL" name="EMAIL" type="email">'
                "</form></div>\n<p>final</p>")
        out = _apply(html, "subscribe-forms")
        assert "list-manage" not in out and "mce-EMAIL" not in out
        assert "Abonează-te" not in _text(out)
        assert "articol" in _text(out) and "final" in _text(out)

    def test_removes_mailerlite_embed(self):
        html = ('<p>articol</p><div class="ml-form-embedContainer ml-subscribe-form">'
                '<form class="ml-block-form" action="https://assets.mailerlite.com/'
                'jsonp/1/forms/2"><input type="email" name="fields[email]">'
                '<button class="primary">Abonează-te!</button></form></div>')
        out = _apply(html, "subscribe-forms")
        assert "mailerlite" not in out and "Abonează-te" not in _text(out)
        assert "articol" in _text(out)

    def test_removes_mautic_block(self):
        html = ('<p>articol</p><div class="news-card hp-news">'
                '<div class="hp-news-form"><div class="mauticform_wrapper" '
                'id="mauticform_wrapper_subscribeform">'
                '<form id="mauticform_subscribeform" action="https://x/form/submit?formId=1">'
                '<input name="mauticform[email]" type="email"></form></div></div></div>')
        out = _apply(html, "subscribe-forms")
        assert "mautic" not in out
        assert "articol" in _text(out)

    def test_removes_site_search_form(self):
        """pressone shipped the navbar search box, not a newsletter."""
        html = ('<p>articol</p><nav class="navbar"><div class="navbar-collapse">'
                '<span class="search-form-container">'
                '<form action="/search"><input type="text" name="search" '
                'class="search-form-input"></form></span></div></nav>')
        out = _apply(html, "subscribe-forms")
        assert "<form" not in out and "search-form" not in out
        assert "articol" in _text(out)

    def test_removes_unrecognised_block_by_boilerplate_text(self):
        html = ('<p>articol</p><section><h3>Înscrie-te la newsletter</h3>'
                "<p>Primește zilnic articolele noastre. Te poți dezabona oricând.</p>"
                "</section>")
        out = _apply(html, "subscribe-forms")
        assert "dezabona" not in _text(out)
        assert "articol" in _text(out)

    def test_prunes_wrappers_left_hollow(self):
        """Removing the widget left a positioning sibling holding nothing."""
        html = ('<p>articol</p><div class="col-xxl-8 col-12">'
                '<div class="newsletter-box footer-nsl">'
                '<img src="https://track.mailerlite.com/webforms/o/1/u2" height="1">'
                '</div><div class="nsl-art-2 col-12 mx-auto"></div></div>')
        out = _apply(html, "subscribe-forms")
        assert "nsl-art-2" not in out
        assert "track.mailerlite" not in out
        assert "articol" in _text(out)

    def test_keeps_a_wrapper_that_holds_real_content(self):
        """The text fallback must not eat a block that also holds the article."""
        html = ('<div><p>Continuarea articolului despre newsletter.</p>'
                "<p>Înscrie-te la newsletter</p></div>")
        out = _apply(html, "subscribe-forms")
        assert "Continuarea articolului" in _text(out)

    def test_keeps_article_images_and_links(self):
        html = ('<p>articol</p><img src="https://x.ro/uploads/foto.jpg">'
                '<a href="https://x.ro/alt">alt</a>')
        out = _apply(html, "subscribe-forms")
        assert "foto.jpg" in out and 'href="https://x.ro/alt"' in out


class TestEmojiImageHostMatching:
    """`emoji-images` decides what is a Facebook glyph and what is a photo.

    It used to ask whether the string "fbcdn.net" appeared anywhere in the
    src. That is a substring test on a URL, so it also fired on an image whose
    *query string* mentioned the host, and on any host that merely contained
    the word "emoji" -- either of which deletes a real photo out of an article.
    """

    def test_a_real_facebook_emoji_is_removed(self):
        out = _apply(
            '<p>a <img src="https://static.xx.fbcdn.net/images/emoji.php/v9/x.png"> b</p>',
            "emoji-images",
        )
        assert "fbcdn" not in out
        assert "a" in _text(out) and "b" in _text(out)

    def test_a_photo_whose_query_mentions_fbcdn_is_kept(self):
        out = _apply(
            '<p><img src="https://uploads.example.ro/foto.jpg?ref=fbcdn.net"></p>',
            "emoji-images",
        )
        assert "foto.jpg" in out

    def test_a_host_containing_the_word_emoji_is_kept(self):
        """The old test was `"emoji" in src.lower()` over the whole URL, so a
        CDN literally named that took the image with it."""
        out = _apply('<p><img src="https://emoji-cdn.example.ro/cover.jpg"></p>', "emoji-images")
        assert "cover.jpg" in out

    def test_an_emoji_in_the_path_is_still_removed(self):
        out = _apply('<p><img src="https://cdn.example.ro/img/emoji/smile.png"></p>', "emoji-images")
        assert "smile.png" not in out

    def test_a_lazy_loaded_emoji_is_removed_via_data_src(self):
        out = _apply('<p><img data-src="https://static.xx.fbcdn.net/e/1.png"></p>', "emoji-images")
        assert "fbcdn" not in out

    def test_an_ordinary_photo_is_untouched(self):
        out = _apply('<p><img src="https://uploads.example.ro/uploads/2026/foto.jpg"></p>',
                     "emoji-images")
        assert "foto.jpg" in out
