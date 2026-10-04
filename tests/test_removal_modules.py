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


class TestModuleOrderIsIrrelevant:
    """A module that rebuilds the document (cosmetic-filters is the only one)
    used to be held back as the final answer while the modules after it kept
    mutating the soup that answer discarded. 23 of the 38 sites that use
    cosmetic-filters list it first, so everything they name after it was
    silently dropped on every run."""

    def test_a_rebuilding_module_does_not_discard_later_ones(self):
        html = (
            '<div class="crp_related"><p>Related Posts:</p></div>'
            '<form id="mc_embed_signup"><input name="EMAIL"></form>'
            "<p>The actual article sentence.</p>"
        )
        out = apply_modules(html, ["cosmetic-filters", "subscribe-forms"])
        assert "mc_embed_signup" not in out
        assert "Related Posts" not in out
        assert "The actual article sentence." in out

    def test_the_result_does_not_depend_on_the_order(self):
        html = (
            '<div class="crp_related"><p>Related Posts:</p></div>'
            '<form id="mc_embed_signup"><input name="EMAIL"></form>'
            "<p>The actual article sentence. AT&amp;T &amp;amp; keep.</p>"
        )
        forward = apply_modules(html, ["cosmetic-filters", "subscribe-forms", "head-meta"])
        backward = apply_modules(html, ["head-meta", "subscribe-forms", "cosmetic-filters"])
        assert forward == backward

    def test_running_it_again_does_not_grow_the_escaping(self):
        """fix_feeds re-applies every site's removals on each run, and an earlier
        version added an `&amp;` layer per pass."""
        html = "<p>AT&amp;T and &amp;amp; here.</p><p>a second sentence.</p>"
        once = apply_modules(html, ["subscribe-forms"])
        for _ in range(5):
            assert apply_modules(once, ["subscribe-forms"]) == once

    def test_a_later_rebuild_supersedes_an_earlier_in_place_edit(self):
        """Reverting the direction of the mistake: an in-place edit after a
        rebuild must be reflected in the result, so the string cannot be
        returned unchanged."""
        html = (
            '<div class="crp_related"><p>Related Posts:</p></div>'
            '<form id="mc_embed_signup"><input name="EMAIL"></form>'
            "<p>articol</p>"
        )
        out = apply_modules(html, ["subscribe-forms", "cosmetic-filters", "head-meta"])
        assert "Related Posts" not in out and "mc_embed_signup" not in out


def test_registry_is_populated():
    """Every module the scan justified must exist."""
    expected = {"comments", "akismet-notice", "sponsor-block", "emoji-images",
                "related-posts", "social-share", "head-meta", "the-tags",
                "blank-embeds"}
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

    def test_removes_apador_chs_underscored_block_with_its_thumbnails(self):
        """Altruista's block is `div.related_posts`, an underscore -- the selector
        above is `div.related-posts` -- so it shipped in the feed for 10 items.
        The block is mostly images: six 180x180 thumbnails of *other* posts."""
        html = (
            '<div class="entry-content"><p>Mai mult despre legea asta.</p></div>'
            '<div class="related_posts clearfix av-related-style-full">'
            '<h5 class="related_title">Citeste si:</h5>'
            '<div class="related_entries_container">'
            '<span class="related_image_wrap">'
            '<img src="https://apador.org/wp-content/uploads/2014/06/rosiianu-180x180.jpg">'
            "</span><strong class=\"av-related-title\">Un alt articol</strong>"
            "</div></div>"
        )
        out = _apply(html, "related-posts")
        assert "related_posts" not in out
        assert "rosiianu-180x180.jpg" not in out
        assert "Mai mult despre legea asta." in out

    def test_keeps_an_article_that_writes_the_phrases_it_would_match_on(self):
        """The text fallback escalates to the nearest div, and on APADOR-CH that
        is `div.entry-content` -- the article body. Its own posts use "Citește și"
        and "Articole similare" in prose, so matching those phrases as text took
        whole articles with them (one item fell to 2% of its text). The named
        container is the only thing that may be removed."""
        html = (
            '<div class="entry-content"><p>Sunt disponibile și variantele '
            "Citește și în articole similare.</p></div>"
            '<div class="related_posts"><h5 class="related_title">Citeste si:</h5>'
            "<p>Un alt articol</p></div>"
        )
        out = _apply(html, "related-posts")
        assert "Citește și" in out, out
        assert "Un alt articol" not in out


class TestBlockParentRefusesToClimbOutOfProse:
    """`_block_parent` is shared by every text-matching module, and its climb is
    what makes them dangerous: from a sentence in a paragraph it reaches the
    nearest div, which on WordPress is the article body."""

    def test_a_phrase_in_prose_yields_no_host(self):
        html = ('<div class="entry-content"><p>Varianta Citește și apare în '
                "articole similare.</p></div>")
        out = _apply(html, "related-posts")
        assert "Citește și" in out

    def test_the_notice_paragraph_itself_is_still_removable(self):
        """akismet-notice asks for `("p",)` because its notice *is* a paragraph.
        Refusing every match inside one would silence the module."""
        html = "<p>articol</p><p>Acest site folosește Akismet pentru a reduce spamul.</p>"
        assert "Akismet" not in _apply(html, "akismet-notice")

    def test_a_heading_label_still_reaches_its_block(self):
        html = ('<p>articol</p><aside class="edu-article__related">'
                "<h2>Articole similare</h2><p>Un alt articol</p></aside>")
        out = _apply(html, "related-posts")
        assert "Un alt articol" not in out
        assert "articol" in out


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


# The shapes below are trimmed from the four feeds that shipped them. Every one
# is a placeholder a browser or a consent plugin swaps out before painting, and
# a feed carries no script -- so the reader draws the box at the size the page
# declared and nothing ever fills it.
_WP_ROCKET_PLACEHOLDER = (
    '<iframe allowfullscreen="allowfullscreen" data-lazy-src="https://www.youtube'
    '.com/embed/VL-xjzQFWsY" data-rocket-lazyload="fitvidscompatible" frameborder="0"'
    ' height="315" loading="lazy" src="about:blank" title="YouTube video player"'
    ' width="560"></iframe>'
)
_YOUTUBE_FRAME = (
    '<iframe allowfullscreen="allowfullscreen" frameborder="0" height="315"'
    ' src="https://www.youtube.com/embed/VL-xjzQFWsY" title="YouTube video player"'
    ' width="560"></iframe>'
)
_LAZYLOAD_FRAME = (
    '<iframe allowfullscreen="allowfullscreen" class="lazyload"'
    ' data-src="https://www.youtube.com/embed/kDnTmNIKbKQ" frameborder="0"'
    ' height="750"></iframe>'
)
# CookieYes paints its poster from a stylesheet the reader never loads.
_COMPLIANZ_PLACEHOLDER = (
    '<iframe allow="autoplay; encrypted-media" class="cmplz-placeholder-element'
    ' cmplz-iframe cmplz-iframe-styles cmplz-video" data-category="marketing"'
    ' data-cmplz-target="src" data-deferlazy="1"'
    ' data-placeholder-image="https://recorder.ro/wp-content/uploads/complianz'
    '/placeholders/youtube-hqdefault.webp" data-service="youtube"'
    ' height="315" src="about:blank" width="560"></iframe>'
)


class TestBlankEmbeds:
    def test_removes_the_wp_rocket_lazy_placeholder(self):
        """computerblog shipped 13 of these: a 315px empty box ahead of the real
        frame, which the theme emits one element later."""
        out = _apply(f"<p>articol</p>{_WP_ROCKET_PLACEHOLDER}{_YOUTUBE_FRAME}", "blank-embeds")
        assert out.count("<iframe") == 1
        assert "about:blank" not in out
        assert "youtube.com/embed/VL-xjzQFWsY" in out

    def test_removes_a_frame_with_no_src_at_all(self):
        """digital-citizen's shape: the placeholder names no document, and the
        real frame sits one element below it."""
        out = _apply(f"<p>articol</p>{_LAZYLOAD_FRAME}{_YOUTUBE_FRAME}", "blank-embeds")
        assert out.count("<iframe") == 1
        assert "lazyload" not in out

    def test_removes_the_consent_placeholder(self):
        """recorder and snoop: no src at all, and no real frame beside it -- the
        poster it would show is a background image from a stylesheet."""
        out = _apply(f"<p>articol</p>{_COMPLIANZ_PLACEHOLDER}", "blank-embeds")
        assert "<iframe" not in out
        assert "articol" in _text(out)

    @pytest.mark.parametrize("src", [
        "about:blank",
        "about:srcdoc",
        "  about:blank  ",
        "javascript:void(0)",
        "javascript:;",
        "data:text/html,",
        "#",
        "",
        "   ",
    ])
    def test_removes_every_spelling_of_a_blank_document(self, src):
        html = f'<p>articol</p><iframe src="{src}" height="315"></iframe>'
        assert "<iframe" not in _apply(html, "blank-embeds")

    def test_removes_a_blank_embed_and_object(self):
        html = ('<p>articol</p><embed src="about:blank" type="video/mp4">'
                '<object data="about:blank" type="text/html"></object>')
        out = _apply(html, "blank-embeds")
        assert "<embed" not in out and "<object" not in out

    def test_an_object_is_judged_on_data_not_src(self):
        """<object> has no `src` in its content model -- the URL is `data`.
        Reading `src` would find nothing and delete every object on the page."""
        html = ('<p>articol</p>'
                '<object data="https://example.com/player.swf"></object>')
        assert "<object" in _apply(html, "blank-embeds")

    def test_a_data_less_object_wrapping_a_real_frame_survives(self):
        """The canonical fallback: <object type="text/html"> around an <iframe>
        for a browser that cannot play the format. <object> reads `data`, finds
        nothing, and would delete the wrapper -- taking the video with it."""
        html = ('<p>articol</p><object type="text/html">'
                '<iframe src="https://www.youtube.com/embed/REAL" '
                'height="315"></iframe></object>')
        out = _apply(html, "blank-embeds")
        assert "youtube.com/embed/REAL" in out
        assert "<object" in out

    def test_a_blank_object_wrapping_a_real_frame_survives(self):
        """Same shape, but with a blank `data`: the frame is still the content."""
        html = ('<p>articol</p><object data="about:blank">'
                '<embed src="https://x.com/player.swf"></object>')
        assert "player.swf" in _apply(html, "blank-embeds")

    def test_a_poster_image_counts_as_fallback(self):
        """An <object> holding only an <img> is a poster, not a white box."""
        html = '<p>articol</p><object data="about:blank"><img src="/p.jpg"></object>'
        assert "p.jpg" in _apply(html, "blank-embeds")

    def test_nested_embeds_do_not_raise(self):
        """`decompose()` clears the attributes of every descendant, so judging a
        nested candidate after its parent blew up on a detached tag. The raise
        escaped the module, and because enrich_article_feed writes its tree only at
        the end, one malformed element left all 30 items on their excerpts."""
        for html in (
            '<p>articol</p><object type="text/html">'
            '<iframe src="https://www.youtube.com/embed/REAL"></iframe></object>',
            '<p>articol</p><object data="about:blank">'
            '<embed src="https://x.com/p.swf"></object>',
            '<p>articol</p><object data="about:blank">'
            '<iframe src="about:blank"></iframe></object>',
            '<p>articol</p><iframe src="about:blank">'
            '<embed src="about:blank"></iframe>',
            '<p>articol</p><object data="about:blank"><div>'
            '<iframe src="about:blank"></iframe></div></object>',
        ):
            out = _apply(html, "blank-embeds")  # must not raise
            assert "articol" in out

    def test_a_placeholder_inside_a_placeholder_is_still_removed(self):
        """The outer shell survives -- it holds markup -- but the empty frame
        inside it is exactly what this module is for."""
        html = ('<p>articol</p><object data="about:blank">'
                '<iframe src="about:blank"></iframe></object>')
        out = _apply(html, "blank-embeds")
        assert "<iframe" not in out
        assert "<p>articol</p>" in out

    def test_two_sibling_placeholders_are_both_removed(self):
        html = ('<p>articol</p><iframe src="about:blank"></iframe>'
                '<iframe src="about:blank"></iframe>')
        assert "<iframe" not in _apply(html, "blank-embeds")

    def test_keeps_a_frame_whose_document_is_written_inline(self):
        html = ('<p>articol</p><iframe srcdoc="&lt;p&gt;inline&lt;/p&gt;"'
                ' height="200"></iframe>')
        assert "<iframe" in _apply(html, "blank-embeds")

    def test_keeps_a_real_embed(self):
        out = _apply(f"<p>articol</p>{_YOUTUBE_FRAME}", "blank-embeds")
        assert out.count("<iframe") == 1

    def test_leaves_the_article_untouched(self):
        html = f'<p>Un articol cu două propoziții.</p>{_WP_ROCKET_PLACEHOLDER}'
        out = _apply(html, "blank-embeds")
        assert "două propoziții" in _text(out)

    def test_running_it_again_changes_nothing(self):
        """fix_feeds re-applies every site's removals on every run."""
        once = _apply(f"<p>articol</p>{_WP_ROCKET_PLACEHOLDER}{_YOUTUBE_FRAME}", "blank-embeds")
        assert _apply(once, "blank-embeds") == once


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

    def test_removes_ghacks_english_badge(self):
        """gHacks ships the same button with an English label, an underscore in
        the class, and a badge *image* -- 30 of them, one per item. It belongs to
        this module and not to cosmetic-filters, because a rule that takes an
        image is refused by the cosmetic image budget."""
        html = (
            "<p>articol</p>"
            '<div class="google-preferred-source-badge">'
            '<a aria-label="Add Ghacks as a preferred source on Google" '
            'href="https://google.com/preferences/source?q=https://www.ghacks.net/">'
            '<img alt="Add Ghacks as a preferred source on Google" src="https://www'
            ".ghacks.net/wp-content/themes/new-ghacks-preview/images/"
            'google-preferred-source-badge-light.png"></a></div>'
        )
        out = _apply(html, "gnews-banner")
        assert "preferred-source-badge" not in out
        assert "articol" in out


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

    def test_removes_the_comment_markers_left_behind_by_the_removed_form(self):
        """revoblog shipped `<!-- Begin MailChimp Signup Form -->` at the end of
        all 7 items. The <form> was removed; the comments around it were not,
        because `find_all(string=...)` yields NavigableStrings and a comment is
        a Comment -- so the markers survived as the only trace of the widget."""
        html = ("<p>articol</p>\n<!-- Begin MailChimp Signup Form -->\n\n"
                "<!-- End MailChimp Signup Form -->\n<p>final</p>")
        out = _apply(html, "subscribe-forms")
        assert "MailChimp" not in out, out
        assert "articol" in out and "final" in out

    def test_keeps_an_unrelated_comment(self):
        """Only the widget's own name is matched, so analytics and other comments
        are left exactly as they were."""
        html = "<p>articol</p><!-- analytics pixel --><p>final</p>"
        assert "analytics pixel" in _apply(html, "subscribe-forms")

    def test_keeps_prose_that_names_the_marker(self):
        """An article about embedding a form quotes the marker as text. In a
        <code> element it is an element, not a comment, so it survives."""
        html = ("<p>Adăugați <code>&lt;!-- Begin MailChimp Signup Form --&gt;</code> "
                "în template.</p>")
        assert "MailChimp" in _apply(html, "subscribe-forms")

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


class TestApplyModulesIsIdempotent:
    """fix_feeds re-applies every site's removals on every deploy, so a pass
    that is not a no-op on already-clean HTML is a bug that compounds."""

    def test_nothing_to_remove_returns_the_input_byte_for_byte(self):
        body = (
            '<article><p>Prose with an ampersand &amp; and a link '
            '<a href="https://example.com/a?b=1&amp;c=2">here</a>.</p></article>'
        )
        assert apply_modules(body, ["cosmetic-filters"]) == body

    def test_repeated_application_does_not_grow_the_document(self):
        body = (
            '<article><p>Prose.</p><div class="crp_related">'
            '<h2>Related Posts:</h2><ul><li><a href="/a">b</a></li></ul></div></article>'
        )
        once = apply_modules(body, ["cosmetic-filters"])
        twice = apply_modules(once, ["cosmetic-filters"])
        assert once == twice

    def test_heavily_escaped_content_is_not_re_escaped(self):
        """The bug this pins: a description already carrying a dozen `&amp;`
        layers grew another layer on every run, because the module returned a
        re-serialised soup even when it had removed nothing. Over a deploy cycle
        that compounds without limit."""
        body = (
            "<div>" + "&amp;" * 12 + "<p>text</p>"
            '<div class="crp_related">Related Posts</div></div>'
        )
        once = apply_modules(body, ["cosmetic-filters"])
        assert "&amp;" * 13 not in once, "an escaping layer was added"
        assert apply_modules(once, ["cosmetic-filters"]) == once

    def test_a_module_that_rebuilds_the_document_wins(self):
        """cosmetic-filters returns a string; it must not be ignored in favour
        of the untouched soup."""
        body = '<div><p>keep</p><div class="cta-tags"><a href="/tag/a">a</a></div></div>'
        out = apply_modules(body, ["cosmetic-filters"])
        assert "keep" in out
        assert "cta-tags" not in out

    def test_an_unknown_module_is_ignored(self):
        body = "<p>unchanged</p>"
        assert apply_modules(body, ["no-such-module"]) == body


class TestShortcodeRemoval:
    """WordPress shortcodes the theme never rendered appear in the body as
    literal text, so the reader is shown the plugin's source: the `[su_note
    note_color=... radius=...]` wrappers on amar-de-zi, `[su_highlight ...]` on
    rapid7, `[su_box ...]` on snoop.
    """

    def test_a_matched_pair_is_removed_whole(self):
        out = apply_modules(
            "<p>before</p><p>[su_note note_color=x radius=10]Notele mele[/su_note]</p><p>after</p>",
            ["shortcodes"],
        )
        assert "su_note" not in out
        assert "Notele mele" in out, "the prose the shortcode wrapped was removed"
        assert "before" in out and "after" in out

    def test_both_halves_go(self):
        """Removing only the opening tag leaves `[/su_note]` on its own, which
        is how the first version of this behaved."""
        out = apply_modules("<p>[su_box title=x]body[/su_box]</p>", ["shortcodes"])
        assert "su_box" not in out
        assert "body" in out

    def test_two_pairs_in_one_text_node(self):
        out = apply_modules(
            "<p>[su_note a=1]one[/su_note][su_box b=2]two[/su_box]</p>", ["shortcodes"]
        )
        assert "su_note" not in out and "su_box" not in out
        assert "onetwo" in out.replace(" ", "")

    @pytest.mark.parametrize(
        "body",
        [
            '<p>[role="img"] text</p>',            # CSS fragment, no closing tag
            "<p>[active=true] toggle</p>",          # template fragment
            "<p>see [continues below] for more</p>",  # ordinary prose
            "<p>price [100] and [200] range</p>",    # bare numbers
        ],
    )
    def test_something_without_a_closing_tag_is_left_alone(self, body):
        """The closing tag is the discriminator, and it has to be. A pattern
        matching any `[name key="value"]` also caught `[role="img"]` and
        `[active=true]` in snoop and `[data-rmiz-content="found"]` 84 times in
        rapid7 -- CSS and template fragments, with nothing to do with
        shortcodes."""
        assert apply_modules(body, ["shortcodes"]) == body

    def test_it_is_a_noop_on_markup_with_nothing_to_remove(self):
        body = '<p>An ordinary paragraph with a <a href="https://x">link</a>.</p>'
        assert apply_modules(body, ["shortcodes"]) == body

    def test_rewriting_a_node_keeps_it_separated_from_inline_tags(self):
        """Stripping the rebuilt text glued it to whatever inline tag came
        before: `a <b>bold</b> [su_note]x[/su_note] b` came out as
        `<b>bold</b>x b`, with "bold" and "x" run into one word."""
        out = apply_modules("<p>a <b>bold</b> [su_note c=1]x[/su_note] b</p>", ["shortcodes"])
        assert "<b>bold</b>x" not in out, out
        assert "bold</b> x" in out or ("bold</b>" in out and " x" in out), out

    def test_a_node_that_is_entirely_a_shortcode_is_emptied_not_glued(self):
        out = apply_modules("<p>before <span>[su_note c=1]x[/su_note]</span> after</p>", ["shortcodes"])
        assert "before" in out and "after" in out
        assert "su_note" not in out



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
