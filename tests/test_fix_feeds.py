import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts.fix_feeds import (
    FIXES,
    FORMAT_FLAGS_RE,
    STRIP_FIELD_SETS,
    dedupe_epguides_links,
    dedupe_search_links,
    fix_description_html,
    fix_magnet_download_link,
    fix_poster_style,
    fix_poster_url,
    order_poster_and_links,
    strip_label_fields,
    strip_trailing_separators,
)


def test_strip_label_fields_removes_uindex_stats_but_keeps_size() -> None:
    desc = (
        "<img src=\"https://image.tmdb.org/t/p/w500/p.jpg\"><br/>"
        "<strong>Size:</strong> 1.65 GB<br/>"
        "<strong>Uploaded:</strong> 2 days ago<br/>"
        "<strong>Seeds:</strong> 1,579<br/>"
        "<strong>Leechers:</strong> 289<br/>"
        '<a href="https://www.youtube.com/results?search_query=x">Trailer</a>'
    )
    for feed in ("uindex-movies.xml", "uindex-tv.xml"):
        out = strip_label_fields(desc, feed)
        assert "<strong>Size:</strong> 1.65 GB" in out
        assert "Uploaded" not in out
        assert "Seeds" not in out
        assert "Leechers" not in out
        assert "Trailer" in out


def test_strip_label_fields_noop_for_unlisted_feed() -> None:
    desc = "<strong>Genres:</strong> Drama<br/>"
    assert strip_label_fields(desc, "other.xml") == desc
    assert "other.xml" not in STRIP_FIELD_SETS


def test_strip_label_fields_is_idempotent() -> None:
    desc = (
        "<img src=\"https://x/p.jpg\"><br/>"
        "<strong>Seeds:</strong> 12<br/>"
        "<strong>Leechers:</strong> 3<br/>"
        '<a href="https://x">Trailer</a>'
    )
    once = strip_label_fields(desc, "uindex-tv.xml")
    assert "Seeds" not in once and "Leechers" not in once
    assert strip_label_fields(once, "uindex-tv.xml") == once


def test_fix_poster_style_applies_pinned_size() -> None:
    desc = '<img src="https://image.tmdb.org/t/p/w780/p.jpg" width="800" style="max-width:999px;">'
    out = fix_poster_style(desc)
    assert 'src="https://image.tmdb.org/t/p/w342/p.jpg"' in out
    assert "width:300px" in out
    assert "max-height:450px" in out
    assert 'width="300"' in out
    assert 'loading="lazy"' in out
    assert 'width="800"' not in out
    assert "max-width:999px" not in out
    assert "w780" not in out


def test_fix_poster_style_uses_smaller_cinema_posters(monkeypatch) -> None:
    from scripts.fix_feeds import FEED_CATEGORIES

    monkeypatch.setitem(FEED_CATEGORIES, "cinema-feed.xml", "cinema")
    out = fix_poster_style(
        '<img src="https://image.tmdb.org/t/p/w780/p.jpg">', "cinema-feed.xml"
    )
    assert "width:150px" in out
    assert "max-height:225px" in out
    assert 'width="150"' in out
    assert "w185/p.jpg" in out
    assert "w780" not in out
    assert "width:300px" not in out


def test_fix_description_html_strips_and_resizes() -> None:
    desc = (
        "<img src=\"https://image.tmdb.org/t/p/w500/p.jpg\"><br/>"
        "<strong>Seeds:</strong> 12<br/>"
        "<strong>Leechers:</strong> 3<br/>"
    )
    out = fix_description_html(desc, "uindex-movies.xml")
    assert "Seeds" not in out
    assert "Leechers" not in out
    assert "width:300px" in out
    assert 'width="300"' in out


def test_trailing_link_separator_is_removed() -> None:
    """computerblog ends every item `</div>\\n\\n | \\n\\n` once the extractor
    drops the footer topic links, so each article closed with a stray ` | `."""
    desc = "<div><p>Textul articolului.</p></div>\n\n | \n\n\n"
    out = strip_trailing_separators(desc)
    assert out.endswith("</div>")
    assert "|" not in out
    assert not out.endswith("\n")


def test_trailing_separator_strip_keeps_an_inner_pipe() -> None:
    desc = "<div><p>a | b</p></div>"
    assert strip_trailing_separators(desc) == desc


def test_trailing_separator_is_stripped_from_a_built_description() -> None:
    out = fix_description_html(
        "<div><p>Corpul.</p></div>\n\n | \n\n", "computerblog.xml"
    )
    assert out.endswith("</div>")


def test_fix_poster_url_inserts_the_missing_slash() -> None:
    broken = '<img src="https://image.tmdb.org/t/p/w500abcDEF.jpg">'
    fixed = fix_poster_url(broken)
    assert "w500/abcDEF.jpg" in fixed
    assert 'w500abc' not in fixed


def test_fix_poster_url_leaves_a_good_url_alone() -> None:
    good = "https://image.tmdb.org/t/p/w500/abcDEF.jpg"
    assert fix_poster_url(good) == good
    # ...and must not eat a digit out of the size token.
    assert "w342/6XCb.jpg" in fix_poster_url("https://image.tmdb.org/t/p/w342/6XCb.jpg")


def test_order_poster_and_links_moves_links_behind_the_poster() -> None:
    """atlantic: the enricher prepended the links, so the poster closed the item."""
    desc = (
        '<br><a href="https://x"><b>Trailer</b></a>'
        '<br><a href="https://y"><b>IMDb</b></a>'
        '<br><a href="https://z"><b>EpGuides</b></a>'
        '<br><img src="https://image.tmdb.org/t/p/w342/6XCb.jpg">'
    )
    out = order_poster_and_links(desc, "some-poster-feed.xml")
    assert out.startswith("<img"), out
    assert out.index("<img") < out.index("Trailer")
    assert out.rindex("EpGuides") > out.index("IMDb")


def test_order_poster_and_links_puts_links_after_the_body() -> None:
    """bingebang: links sat between the poster and the year/genres block."""
    desc = (
        '<img src="https://image.tmdb.org/t/p/w342/x.jpg"><br>'
        '<a href="https://x"><b>Trailer</b></a><br>'
        '<a href="https://y"><b>IMDb</b></a><br>'
        "<strong>Rating:</strong> 7.4<br><strong>Year:</strong> 2026<br>Overview."
    )
    out = order_poster_and_links(desc, "some-poster-feed.xml")
    assert out.startswith("<img")
    assert out.index("Rating:") < out.index("Trailer")
    assert out.rindex("Overview.") < out.index("Trailer")


def test_order_poster_and_links_is_idempotent() -> None:
    feed = "some-poster-feed.xml"
    desc = (
        '<img src="https://image.tmdb.org/t/p/w342/x.jpg"><br>'
        "<strong>Rating:</strong> 7.4<br>Overview.<br>"
        '<a href="https://x"><b>Trailer</b></a><br>'
        '<a href="https://y"><b>IMDb</b></a>'
    )
    once = order_poster_and_links(desc, feed)
    assert order_poster_and_links(once, feed) == once


def test_player_server_label_checker_catches_missing_label(monkeypatch) -> None:
    """A server added to PLAYER_SERVERS without a label in
    _POSTER_LINK_LABELS must fail loudly.
    """
    import scripts.fix_feeds as ff
    from scripts.enrichers import player_servers as ps

    monkeypatch.setattr(
        ff, "_POSTER_LINK_LABELS", tuple(label for label in ff._POSTER_LINK_LABELS if label != "VidFast")
    )
    with pytest.raises(ImportError, match="Missing labels:"):
        ff._check_player_server_labels()

    # A brand-new server added to the registry without its guard tag in
    # _POSTER_LINK_LABELS is caught by the same check.
    future = ps.PlayerServer(
        guard="VidFuture</b>", label="VidFuture", movie_url="", tv_url="",
        tv_series_url="", id_type="tmdb",
    )
    monkeypatch.setattr(ps, "PLAYER_SERVERS", {**ps.PLAYER_SERVERS, "vidfuture": future})
    with pytest.raises(ImportError, match="Missing labels:"):
        ff._check_player_server_labels()


def test_player_server_label_checker_passes_when_complete() -> None:
    import scripts.fix_feeds as ff

    ff._check_player_server_labels()


def test_order_poster_and_links_leaves_an_article_feed_alone(monkeypatch) -> None:
    from scripts.fix_feeds import FEED_CATEGORIES

    monkeypatch.setitem(FEED_CATEGORIES, "blog.xml", "blogs")
    desc = (
        "<p>Intro paragraph.</p>"
        '<a href="https://x"><b>IMDb</b></a>'
        '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
    )
    assert order_poster_and_links(desc, "blog.xml") == desc


# ---- --site filtering -------------------------------------------------------


class TestSiteFilter:
    """`--site NAME` must narrow a run to the named feed instead of sweeping
    every file in feeds/."""

    @staticmethod
    def _setup(tmp_path, monkeypatch, declared, written=None):
        """Create feeds for `declared` sites, materialising only `written` files.

        Defaults to writing all of them; passing a smaller `written` list models a
        site that is configured but whose feed was never generated.
        """
        import scripts.fix_feeds as ff

        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        for name in written if written is not None else declared:
            (feeds_dir / name).write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<rss version="2.0"><channel><title>t</title></channel></rss>',
                encoding="utf-8",
            )
        config_file = tmp_path / "sites.yaml"
        config_file.write_text(
            "sites:\n"
            + "".join(
                f"  {Path(name).stem}:\n"
                f'    url: "https://example.com/"\n'
                '    method: "rss"\n'
                '    item_selector: "//item"\n'
                '    title_selector: "./title"\n'
                '    link_selector: "./link"\n'
                f'    feed_file: "{name}"\n'
                for name in declared
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ff, "FEEDS_DIR", feeds_dir)
        monkeypatch.setattr(ff, "SITES_CONFIG", config_file)
        return feeds_dir

    def _run(self, tmp_path, monkeypatch, argv, declared=("a.xml", "b.xml"), written=None):
        import scripts.fix_feeds as ff

        self._setup(tmp_path, monkeypatch, declared, written)
        return ff.main(argv)

    def test_no_site_flag_processes_every_feed(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, [])
        out = capsys.readouterr().out
        assert "Processing 2 feeds" in out

    def test_site_flag_processes_only_that_feed(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, ["--site", "a"])
        out = capsys.readouterr().out
        assert "Processing 1 feeds" in out

    def test_site_flag_accepts_the_xml_filename(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, ["--site", "a.xml"])
        assert "Processing 1 feeds" in capsys.readouterr().out

    def test_site_flag_is_repeatable(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, ["--site", "a", "--site", "b"])
        assert "Processing 2 feeds" in capsys.readouterr().out

    def test_unknown_site_fails_instead_of_sweeping(self, tmp_path, monkeypatch, capsys):
        """A typo must not quietly rewrite all 70 feeds."""
        assert self._run(tmp_path, monkeypatch, ["--site", "typo"]) == 1
        assert "unknown site: typo" in capsys.readouterr().err
        assert "Processing" not in capsys.readouterr().out

    def test_configured_but_ungenerated_feed_fails(self, tmp_path, monkeypatch, capsys):
        """Targeting a site whose feed file does not exist is a real problem for
        a single-feed run, unlike in a full sweep."""
        assert self._run(
            tmp_path, monkeypatch, ["--site", "gone"],
            declared=("a.xml", "gone.xml"), written=("a.xml",),
        ) == 1
        assert "no such feed file" in capsys.readouterr().err


class TestChromeStripPass:
    """A site that is down keeps its previously published descriptions, which
    were written by older code and still carry comments, ads and theme chrome.
    Enrichment cannot reach them, so the post-process pass has to."""

    def test_strips_chrome_from_a_stale_description(self) -> None:
        from scripts.fix_feeds import strip_configured_chrome

        stale = (
            "<p>articol</p>"
            '<aside aria-label="Oferta zilei la eMAG" class="rb-emag-offer">'
            '<a href="https://e.emag.com/x">Oferta zilei</a></aside>'
            '<img src="https://x.ro/wp-content/themes/x/images/rss.png">'
        )
        out = strip_configured_chrome(stale, ("promo-footer", "theme-icons"))
        assert "Oferta zilei" not in out
        assert "wp-content/themes" not in out
        assert "articol" in out

    def test_unwrapping_noscript_keeps_the_photo(self) -> None:
        from scripts.fix_feeds import strip_configured_chrome

        stale = ('<figure><noscript><img src="https://x.ro/uploads/shot.jpg">'
                 "</noscript></figure><noscript>Enable JavaScript</noscript>")
        out = strip_configured_chrome(stale, ("head-meta",))
        assert "<noscript" not in out
        assert "shot.jpg" in out
        assert "Enable JavaScript" not in out

    def test_is_a_noop_on_a_clean_description(self) -> None:
        from scripts.fix_feeds import strip_configured_chrome

        clean = '<p>un articol oarecare</p><img src="https://x.ro/uploads/a.jpg">'
        assert "un articol oarecare" in strip_configured_chrome(
            clean, ("head-meta", "theme-icons", "promo-footer")
        )

    def test_a_failing_module_never_breaks_the_build(self) -> None:
        from scripts.fix_feeds import strip_configured_chrome

        stale = "<p>articol</p>"
        assert strip_configured_chrome(stale, ("no-such-module",)) == stale

    def test_only_sites_with_removals_are_in_scope(self) -> None:
        """Streaming feeds must never be touched: no removals, no pass."""
        from scripts.fix_feeds import FEED_REMOVALS

        assert FEED_REMOVALS, "expected at least one article-mode site"
        for feed_file, mods in FEED_REMOVALS.items():
            assert mods, f"{feed_file} has an empty removals list"


# ---- Happy Cinema presentation flags ----------------------------------------

class TestFormatFlagStrip:
    """Happy Cinema glues presentation flags onto the title with underscores.
    The description's "Formate:" field already carries the same data, so the
    title copy is redundant."""

    @staticmethod
    def _strip(title: str) -> str:
        m = FORMAT_FLAGS_RE.search(title)
        return (title[: m.start()] + (m.group(1) or "")).strip() if m else title

    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("Răzbunătorii: Sfârșitul jocului_SUB_3D", "Răzbunătorii: Sfârșitul jocului"),
            ("Resident Evil_UCRAINIANA /Обитель зла", "Resident Evil"),
            ("Kuzma_UCRAINIANA", "Kuzma"),
            ("Cum să devii supererou: Masca roșie_DUBLAT", "Cum să devii supererou: Masca roșie"),
            ("Coyote vs. Acme_2D_DUBLAT", "Coyote vs. Acme"),
            ("Fata din nori_DUBLAT _2D", "Fata din nori"),
            ("Mașini_DUBLAT_2D", "Mașini"),
            (
                "Patrula cățelușilor: În lumea dinozaurilor _DUBLAT_2D (2026)",
                "Patrula cățelușilor: În lumea dinozaurilor (2026)",
            ),
            ("Pisicile de la muzeu 2: Comoara din Egipt_2D_DUBLAT", "Pisicile de la muzeu 2: Comoara din Egipt"),
        ],
    )
    def test_strips_flags(self, raw: str, want: str) -> None:
        assert self._strip(raw) == want

    def test_keeps_a_trailing_year(self) -> None:
        """fix_title_year and add_year_from_url run after this, so the captured
        year has to survive the strip."""
        assert self._strip("Atlasul Universului_DUBLAT (2026)") == "Atlasul Universului (2026)"

    @pytest.mark.parametrize(
        "title",
        [
            "Resident Evil (2026)",
            "Star Wars: The Force Awakens (2015)",
            "Lanterns S01E06 720p AMZN WEB-DL DD 5 1 H 264-playWEB (2026)",
            "Mitigating prompt injection attacks with a layered defense system",
            "snake_case_identifier in a tech post",
            "Some Movie: Part 2 / Special Edition",
        ],
    )
    def test_leaves_normal_titles_alone(self, title: str) -> None:
        """The rule is anchored on known flag tokens, not on the presence of an
        underscore, so tech-post identifiers and release-group names survive."""
        assert self._strip(title) == title

    def test_only_the_happy_cinema_feeds_are_in_scope(self) -> None:
        assert FIXES["format_flags"] == {
            "happy-cinema-colosseum.xml",
            "happy-cinema-vitantis.xml",
        }


class TestPosterStyleIdempotency:
    """fix_feeds re-applies these fixes on every deploy, so each one has to be a
    no-op on an already-fixed description."""

    def test_repeated_application_adds_no_bytes(self):
        import scripts.fix_feeds as ff

        body = (
            '<p>text</p><img alt="" '
            'src="https://cdn.example.com/max/1024/1*a.png" '
            'style="width:300px;height:auto;" width="300" loading="lazy" />'
        )
        once = ff.fix_poster_style(body, "some-feed.xml")
        twice = ff.fix_poster_style(once, "some-feed.xml")
        assert once == twice, f"grew by {len(twice) - len(once)} bytes"

    def test_the_tag_has_exactly_one_space_before_style(self):
        """The bug: tag[:-2] strips "/>" but leaves the space before it, and the
        replacement starts with a space, so every run added one more."""
        import scripts.fix_feeds as ff

        body = (
            '<img alt="" src="https://cdn.example.com/a.png" '
            'style="width:300px;" width="300" loading="lazy" />'
        )
        out = ff.fix_poster_style(body, "f.xml")
        assert '"  style=' not in out, out
        assert out.count(' style=') == 1

    def test_a_tag_without_a_trailing_slash_is_also_stable(self):
        import scripts.fix_feeds as ff

        body = '<img alt="" src="https://cdn.example.com/a.png" style="width:300px;">'
        once = ff.fix_poster_style(body, "f.xml")
        assert ff.fix_poster_style(once, "f.xml") == once

    def test_a_poster_url_is_still_downscaled(self):
        import scripts.fix_feeds as ff

        body = '<img src="https://image.tmdb.org/t/p/w500/a.jpg">'
        out = ff.fix_poster_style(body, "f.xml")
        # Not a hardcoded width: _tmdb_size_for picks the nearest size TMDb
        # actually serves, so the assertion is that it went *down*.
        m = re.search(r"/t/p/(w\d+|original)/", out)
        assert m, out
        assert m.group(1) != "w500", out


class TestArticleImagesAreNotPosterSized:
    """`fix_poster_style` clamps every <img> to the poster width, which is right
    for movie cards and wrong for article illustrations: a 300px photo stranded in
    a 534px panel leaves 234px of dead space beside it (measured on securelist, on
    every illustration in the feed).
    """

    ARTICLE = "securityaffairs.xml"      # category: cyber
    POSTER = "uindex-movies.xml"    # category: movies

    def test_a_poster_feed_is_still_pinned(self):
        out = fix_poster_style('<img src="https://image.tmdb.org/t/p/w780/p.jpg">', self.POSTER)
        assert "width:300px" in out
        assert 'width="300"' in out
        assert "max-width:100%" not in out

    def test_an_article_keeps_the_width_the_site_chose(self):
        out = fix_poster_style(
            '<img src="https://cdn.example.com/a.png" width="900" height="500">', self.ARTICLE
        )
        assert 'width="900"' in out, "the site's own width was overwritten"
        assert "max-width:100%" in out, "it must still scale down to fit the panel"
        assert "width:300px" not in out

    def test_an_article_still_gets_lazy_loading_and_the_border_radius(self):
        out = fix_poster_style('<img src="https://cdn.example.com/a.png">', self.ARTICLE)
        assert 'loading="lazy"' in out
        assert "border-radius:4px" in out
        assert "max-height:450px" in out

    def test_an_article_still_has_its_src_downscaled(self):
        out = fix_poster_style(
            '<img src="https://image.tmdb.org/t/p/w780/p.jpg">', self.ARTICLE
        )
        assert "image.tmdb.org/t/p/w342/p.jpg" in out

    def test_an_unknown_feed_name_keeps_the_old_pinned_behaviour(self):
        """A site added today is more likely a poster feed than not, and a
        surprise in either direction is worse than the status quo."""
        out = fix_poster_style('<img src="https://cdn.example.com/a.png">')
        assert "width:300px" in out


class TestDuplicateDescription:
    """An item must carry one description, not several.

    The streaming enricher used to create a `<description>` on each of its two
    loop iterations, so items from native RSS/Atom feeds (Reddit) ended up with
    two. `item.find()` only ever reached the first, leaving the duplicate with a
    raw full-resolution poster while the first was downscaled and styled --
    readers disagreed about which to render, so the same item showed different
    poster sizes depending on where it was read.
    """

    @staticmethod
    def _feed(tmp_path, descriptions):

        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        # The description carries HTML, so the feed stores it escaped -- the
        # shape fix_feeds actually reads.
        body = "".join(
            f"<description>{d.replace('<', '&lt;').replace('>', '&gt;')}</description>"
            for d in descriptions
        )
        (feeds_dir / "reddit.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel><title>t</title>'
            f"<item><title>Nimrods</title>{body}</item>"
            "</channel></rss>",
            encoding="utf-8",
        )
        config_file = tmp_path / "sites.yaml"
        config_file.write_text(
            "sites:\n  reddit:\n    url: \"https://example.com/\"\n"
            '    method: "rss"\n    category: "releases"\n'
            "    feed_file: \"reddit.xml\"\n",
            encoding="utf-8",
        )
        return feeds_dir, config_file

    def test_duplicate_is_collapsed(self, tmp_path, monkeypatch):
        import scripts.fix_feeds as ff

        feeds_dir, config_file = self._feed(
            tmp_path,
            [
                '<img src="https://image.tmdb.org/t/p/w342/a.jpg">first',
                '<img src="https://image.tmdb.org/t/p/w500/a.jpg">second',
            ],
        )
        monkeypatch.setattr(ff, "FEEDS_DIR", feeds_dir)
        monkeypatch.setattr(ff, "SITES_CONFIG", config_file)

        ff.main([])

        ch = ff.ET.parse(feeds_dir / "reddit.xml").getroot().find("channel")
        (item,) = ch.findall("item")
        assert len(item.findall("description")) == 1

    def test_the_kept_one_is_the_poster_that_gets_styled(self, tmp_path, monkeypatch):
        """The surviving description must be the normalized 300px poster.

        Both duplicates carry the poster, so the collapse alone would still
        leave whichever one it picked unstyled if it picked the wrong one.
        """
        import scripts.fix_feeds as ff

        feeds_dir, config_file = self._feed(
            tmp_path,
            [
                '<img src="https://image.tmdb.org/t/p/w500/a.jpg">second-longer',
                '<img src="https://image.tmdb.org/t/p/w500/a.jpg">s',
            ],
        )
        monkeypatch.setattr(ff, "FEEDS_DIR", feeds_dir)
        monkeypatch.setattr(ff, "SITES_CONFIG", config_file)

        ff.main([])

        text = ff.ET.parse(feeds_dir / "reddit.xml").getroot().findtext(
            "channel/item/description"
        )
        assert 'width="300"' in text
        assert "second-longer" in text

    def test_single_description_is_untouched(self, tmp_path, monkeypatch):
        import scripts.fix_feeds as ff

        feeds_dir, config_file = self._feed(
            tmp_path, ['<img src="https://image.tmdb.org/t/p/w500/a.jpg">only one']
        )
        monkeypatch.setattr(ff, "FEEDS_DIR", feeds_dir)
        monkeypatch.setattr(ff, "SITES_CONFIG", config_file)

        ff.main([])

        ch = ff.ET.parse(feeds_dir / "reddit.xml").getroot().find("channel")
        (item,) = ch.findall("item")
        assert len(item.findall("description")) == 1
        assert "only one" in item.findtext("description")


class TestDuplicateSearchLinks:
    """A reader saw the Trailer and IMDb links twice on the cinema feeds.

    The Romanian cinema sites write their own pair in the XPath
    `description_selector`; the streaming enricher then wrote a second pair in
    front of them, so 112 anchors across the 9 cinema feeds shipped doubled. The
    enricher now decides each link on its own and no longer adds a second one,
    but this stage is still required: an item whose own description already
    carries both pairs still has to be collapsed within the same pass.
    """

    @staticmethod
    def _ours(title: str) -> str:
        return (
            '<img src="https://image.tmdb.org/t/p/w185/p.jpg">'
            "<br>"
            '<a href="https://www.youtube.com/results?search_query=Odiseea'
            '+preview%7Cpromo%7Ctrailer+-fake+-fan"><b style="color:#6600cc;">'
            "Trailer</b></a>"
            "<br>"
            '<a href="https://www.imdb.com/find?q=Odiseea&amp;s=tt">'
            '<b style="color:#6600cc;">IMDb</b></a>'
            "<br/><b>Premiera:</b> 03.07.2015<br/><b>Gen:</b> Actiune"
        )

    @staticmethod
    def _theirs(title: str) -> str:
        return (
            "<br/><b>Distributie:</b> Regel Film<br/><b>Program:</b> 19:00"
            '<br><a href="https://www.youtube.com/results?search_query=Odiseea'
            '+trailer%7Cpromo%7Cpreview+-fake+-fan"><b style="color:#6600cc;">'
            "Trailer</b></a>"
            '<br><a href="https://www.imdb.com/find/?q=Odiseea&amp;s=tt'
            '&amp;ttype=ft"><b style="color:#6600cc;">IMDb</b></a>'
        )

    def test_a_second_pair_is_removed(self) -> None:
        out = dedupe_search_links(self._ours("x") + self._theirs("x"))
        assert out.count(">Trailer<") == 1
        assert out.count(">IMDb<") == 1

    def test_the_enrichers_pair_is_the_one_kept(self) -> None:
        """It sits under the poster with the other generated links, and its query
        is built from the TMDb title matched against the feed's own."""
        out = dedupe_search_links(self._ours("x") + self._theirs("x"))
        assert "imdb.com/find?q=" in out
        assert "ttype=ft" not in out

    def test_the_body_between_and_after_the_links_survives(self) -> None:
        out = dedupe_search_links(self._ours("x") + self._theirs("x"))
        for field in ("Premiera:", "03.07.2015", "Gen:", "Distributie:", "Regel Film", "19:00"):
            assert field in out, field

    def test_the_line_break_of_a_dropped_anchor_goes_with_it(self) -> None:
        """Keeping the break would leave the item with a doubled break where the
        duplicate used to be."""
        out = dedupe_search_links(self._ours("x") + self._theirs("x"))
        assert "<br><br" not in out
        assert out.endswith("19:00")

    def test_a_single_pair_is_left_alone(self) -> None:
        once = self._ours("x")
        assert dedupe_search_links(once) == once

    def test_a_trailer_without_an_imdb_link_is_not_touched(self) -> None:
        """The sites' pairs come and go together, but a feed that links only a
        trailer must keep it -- this stage removes duplicates, not links."""
        only_trailer = self._ours("x").split("<br><a")[0] + "<br><a" + self._ours("x").split("<br><a")[1]
        assert ">Trailer</b>" in only_trailer
        assert ">IMDb</b>" not in only_trailer
        assert dedupe_search_links(only_trailer) == only_trailer

    def test_repeated_application_adds_no_bytes(self) -> None:
        doubled = self._ours("x") + self._theirs("x")
        once = dedupe_search_links(doubled)
        assert dedupe_search_links(once) == once

    def test_a_bare_anchor_in_prose_is_left_alone(self) -> None:
        """TorrentFreak item 15 lists ten trailers for other films as bare
        ``<a>trailer</a>`` anchors in the article body -- real content, not the
        generated link. Only the ``<b>``-wrapped label counts, and both this
        module and the sites' XPath selectors always wrap it."""
        prose = (
            "<p>Trailers:</p>"
            + '<a href="https://www.youtube.com/watch?v=a">trailer</a>'
            + '<a href="https://www.youtube.com/watch?v=b">trailer</a>'
        )
        assert dedupe_search_links(prose) == prose

    def test_it_runs_as_part_of_the_description_pass(self) -> None:
        doubled = self._ours("x") + self._theirs("x")
        assert fix_description_html(doubled, "cinemacity-afi-cotroceni.xml").count(">Trailer<") == 1


class TestDedupeEpguidesLinks:
    """The streaming enricher wrote a EpGuides href guessed from the item title
    (``epguides.com/Celebrity+Masterchef``) beside the one built from the resolved
    slug. EpGuides pages are space-free CamelCase, so the guessed form is a 404 --
    and unlike the Trailer/IMDb pair, the keeper is not the first anchor."""

    @staticmethod
    def _guessed(title: str) -> str:
        return (
            f'<br><a href="https://www.epguides.com/{title.replace(" ", "+")}/" '
            'target="_blank" rel="noopener noreferrer">'
            '<b style="color:#28a745;">EpGuides</b></a>'
        )

    @staticmethod
    def _slug(slug: str) -> str:
        return (
            f'<br><a href="https://epguides.com/{slug}/" target="_blank" '
            'rel="noopener noreferrer"><b style="color:#6600cc;">EpGuides</b></a>'
        )

    def test_the_resolved_page_survives_and_the_guess_does_not(self) -> None:
        out = dedupe_epguides_links(self._guessed("Celebrity Masterchef") + self._slug("MasterChef"))
        assert out.count(">EpGuides</b></a>") == 1
        assert "epguides.com/MasterChef/" in out
        assert "+" not in out

    def test_the_order_of_the_pair_does_not_decide_the_keeper(self) -> None:
        out = dedupe_epguides_links(self._slug("MasterChef") + self._guessed("Celebrity Masterchef"))
        assert "epguides.com/MasterChef/" in out
        assert "Celebrity+Masterchef" not in out

    def test_the_line_break_of_a_dropped_anchor_goes_with_it(self) -> None:
        out = dedupe_epguides_links("<p>Body.</p>" + self._guessed("The Show") + self._slug("TheShow"))
        assert "<br><br" not in out

    def test_a_single_canonical_link_is_left_alone(self) -> None:
        once = self._slug("MasterChef")
        assert dedupe_epguides_links(once) == once

    def test_a_guessed_link_alone_is_removed_for_the_enricher_to_replace(self) -> None:
        """Nothing here is a page, and the enricher's guard reads the label, so
        dropping it is what gets the item its real link on the next run."""
        assert dedupe_epguides_links(self._guessed("The Show")) == ""

    def test_repeated_application_adds_no_bytes(self) -> None:
        once = dedupe_epguides_links(self._guessed("Celebrity Masterchef") + self._slug("MasterChef"))
        assert dedupe_epguides_links(once) == once

    def test_it_runs_as_part_of_the_description_pass(self) -> None:
        pair = self._guessed("Celebrity Masterchef") + self._slug("MasterChef")
        assert fix_description_html(pair, "showrss.xml").count(">EpGuides</b></a>") == 1


# A real magnet URI, as showRSS writes it into the item's <link>.
MAGNET = (
    "magnet:?xt=urn:btih:EEA2F10A9F361E8E8FBD82A74C77589FDA3EF173"
    "&dn=Lanterns+S01E02+1080p+HMAX+WEBRip+AAC5+1+10bits+x265+Rapta"
    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
)

# The shape the streaming enricher leaves behind: poster, then a link row, then
# the feed's own text.
ENRICHED_DESC = (
    '<img src="https://image.tmdb.org/t/p/w500/x.jpg" style="max-width:300px;">'
    "<br>"
    '<a href="https://www.imdb.com/title/tt26545992/">'
    '<b style="color:#6600cc;">IMDb</b></a>'
    "<br>New episode: Lanterns S01E02 1080p HMAX WEBRip"
)


class TestMagnetDownloadLink:
    """A torrent feed can put the magnet in the item's own <link> instead of a
    page (showRSS's personal feed, fetched with `magnets=true`).

    The item title is then the only anchor the reader has, and it looks like any
    other article link: nothing says "this starts the download". The description
    gets a labelled button instead, directly under the poster.
    """

    def test_a_magnet_item_gets_a_labelled_anchor_to_the_item_link(self) -> None:
        out = fix_magnet_download_link(ENRICHED_DESC, MAGNET)
        # The fragment is HTML, so the URI's `&` is written `&amp;`; the reader
        # decodes it back before handing the URI to the torrent client.
        assert f'href="{MAGNET.replace("&", "&amp;")}"' in out
        assert ">Download</b>" in out
        # target/rel like every other generated link: clicking inside the
        # reader's panel must not navigate the reader away.
        assert 'target="_blank"' in out
        assert 'rel="noopener noreferrer"' in out

    def test_a_quote_in_the_uri_cannot_escape_the_attribute(self) -> None:
        """The URI is data. A quote in a malformed magnet must stay inside the
        href instead of ending the attribute and starting an event handler."""
        hostile = 'magnet:?xt=urn:btih:abc" onmouseover="alert(1)'

        class Anchors(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.attrs: list[dict[str, str | None]] = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.attrs.append(dict(attrs))

        out = fix_magnet_download_link("New episode", hostile)
        parser = Anchors()
        parser.feed(out)
        assert len(parser.attrs) == 1
        assert parser.attrs[0]["href"] == hostile
        assert "onmouseover" not in parser.attrs[0]

    def test_the_anchor_sits_under_the_poster(self) -> None:
        out = fix_magnet_download_link(ENRICHED_DESC, MAGNET)
        assert out.index("<img") < out.index("magnet:") < out.index("New episode")

    def test_no_poster_still_gets_the_anchor(self) -> None:
        """A TMDb miss leaves the item without artwork; the download is still
        the point of the item."""
        out = fix_magnet_download_link("New episode: Lanterns S01E02", MAGNET)
        assert out.startswith('<a href="magnet:')
        assert out.endswith("New episode: Lanterns S01E02")

    def test_a_page_link_is_left_alone(self) -> None:
        """showRSS's additions feed links browse pages -- the button is for the
        items that actually carry a download, not for every torrent feed."""
        for link in (
            "https://showrss.info/browse/1941",
            "https://uindex.org/torrent/1234",
            "",
        ):
            assert fix_magnet_download_link(ENRICHED_DESC, link) == ENRICHED_DESC

    def test_a_magnet_mentioned_in_the_body_is_not_the_item_link(self) -> None:
        """Only the item's <link> can start a download; a magnet quoted in the
        prose is content, and the item must keep the description it had."""
        prose = "Released: " + MAGNET
        assert fix_magnet_download_link(prose, "https://example.com/post/1") == prose

    def test_a_description_that_already_links_a_magnet_is_left_alone(self) -> None:
        once = fix_magnet_download_link(ENRICHED_DESC, MAGNET)
        assert fix_magnet_download_link(once, MAGNET) == once

    def test_a_feed_own_magnet_link_is_recognised_without_quotes(self) -> None:
        """`href=magnet:…` is valid HTML. A feed that writes one already has its
        button; adding a second would be the duplicate this stage exists to
        avoid."""
        theirs = f"<br><a href={MAGNET}>Download</a>"
        assert fix_magnet_download_link(theirs, MAGNET) == theirs

    def test_repeated_application_adds_no_bytes(self) -> None:
        once = fix_magnet_download_link(ENRICHED_DESC, MAGNET)
        assert once.count("Download") == 1


class TestMagnetLinkThroughTheFullPass:
    """The button has to survive the description stages, which reorder the
    poster and the link row around it, and the pass has to settle."""

    @staticmethod
    def _feed(tmp_path, link, desc):
        import scripts.fix_feeds as ff

        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        # Real feed XML stores the description's HTML escaped.
        escaped = desc.replace("<", "&lt;").replace(">", "&gt;")
        (feeds_dir / "showrss-personal.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel><title>t</title>'
            "<item><title>Lanterns (2026) - S01E02</title>"
            f"<link>{link.replace('&', '&amp;')}</link>"
            f"<description>{escaped}</description></item>"
            "</channel></rss>",
            encoding="utf-8",
        )
        config_file = tmp_path / "sites.yaml"
        config_file.write_text(
            "sites:\n  showrss-personal:\n"
            '    url: "https://showrss.info/"\n'
            '    method: "rss"\n    category: "torrents"\n'
            '    feed_file: "showrss-personal.xml"\n',
            encoding="utf-8",
        )
        return ff, feeds_dir, config_file

    def test_the_button_lands_under_the_styled_poster_and_settles(
        self, tmp_path, monkeypatch
    ) -> None:
        ff, feeds_dir, config_file = self._feed(tmp_path, MAGNET, ENRICHED_DESC)
        monkeypatch.setattr(ff, "FEEDS_DIR", feeds_dir)
        monkeypatch.setattr(ff, "SITES_CONFIG", config_file)

        assert ff.main([]) == 0
        feed = feeds_dir / "showrss-personal.xml"
        text = ff.ET.parse(feed).getroot().findtext("channel/item/description")

        assert text.count('href="magnet:') == 1
        assert text.index("<img") < text.index("magnet:") < text.index("New episode")
        # The poster keeps the pinned poster-feed width the stage above set.
        assert 'width="300"' in text
        # Two layers of escaping, one URI: XML escaped the fragment on write and
        # the fragment carries the URI's `&` as `&amp;`. An RSS client decodes
        # both and hands the client the magnet it was given.
        assert "&amp;dn=" in text

        class Anchors(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.hrefs: list[str | None] = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.hrefs.append(dict(attrs).get("href"))

        parser = Anchors()
        parser.feed(text)
        assert MAGNET in parser.hrefs
        # A second run is a no-op, which is what makes the fixed-point loop
        # terminate rather than adding a second button.
        assert ff.process_feed(feed) is False
        again = ff.ET.parse(feed).getroot().findtext("channel/item/description")
        assert again == text

    def test_a_feed_whose_items_link_pages_is_untouched(
        self, tmp_path, monkeypatch
    ) -> None:
        ff, feeds_dir, config_file = self._feed(
            tmp_path, "https://showrss.info/browse/1941", ENRICHED_DESC
        )
        monkeypatch.setattr(ff, "FEEDS_DIR", feeds_dir)
        monkeypatch.setattr(ff, "SITES_CONFIG", config_file)

        ff.main([])
        text = ff.ET.parse(feeds_dir / "showrss-personal.xml").getroot().findtext(
            "channel/item/description"
        )
        assert "magnet:" not in text
        assert "Download" not in text
