import re
from pathlib import Path
from urllib.parse import quote

import yaml

from core.config import SiteConfig
from core.feed import generate_rss
from scraper.parser import ParsedItem
from scripts.generate_index import (
    _CATEGORY_GROUP_ALIASES,
    _CATEGORY_SECTIONS,
    _COLUMN_WIDTHS,
    _MEASURED_COLUMN_NEEDS,
    _TABLE_MIN_WIDTH,
    CONFIG_FILE,
    GITHUB_PAGES_FEED_BASE,
    INOREADER_FEED_PREFIX,
    FeedInfo,
    _feed_row_lines,
    _labelled_rows,
    _short_display_name,
    _site_display_name,
    generate_index,
)


def test_generate_index_lists_available_and_unavailable_feeds(tmp_path: Path) -> None:
    config_path = tmp_path / "sites.yaml"
    feeds_dir = tmp_path / "feeds"
    output_file = tmp_path / "index.html"
    output_opml = tmp_path / "feeds.opml"

    feeds_dir.mkdir()
    config_path.write_text(
        """
sites:
  example-ok:
    url: "https://example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "example-ok.xml"
    category: "episodes"
  example-fail:
    url: "https://fail.example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "example-fail.xml"
  example-missing:
    url: "https://missing.example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "example-missing.xml"
  example-movie:
    url: "https://movie.example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "example-movie.xml"
    category: "movies"
  example-release:
    url: "https://release.example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "example-release.xml"
    category: "releases"
  example-other:
    url: "https://other.example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "example-other.xml"
    category: "other"
  example-cinema:
    url: "https://cinema.example.com/"
    method: "http"
    item_selector: "//article"
    title_selector: ".//h2/text()"
    link_selector: ".//a/@href"
    feed_file: "example-cinema.xml"
    category: "cinema"
""",
        encoding="utf-8",
    )

    generate_rss(
        items=[ParsedItem(title="Live item", link="/live", description="Live", pub_date=None)],
        site_name="example-ok",
        site_url="https://example.com/",
        category=None,
        output_path=feeds_dir / "example-ok.xml",
    )
    generate_rss(
        items=[ParsedItem(title="Movie item", link="/movie", description="Movie", pub_date=None)],
        site_name="example-movie",
        site_url="https://movie.example.com/",
        category=None,
        output_path=feeds_dir / "example-movie.xml",
    )
    generate_rss(
        items=[ParsedItem(title="Release item", link="/release", description="Release", pub_date=None)],
        site_name="example-release",
        site_url="https://release.example.com/",
        category=None,
        output_path=feeds_dir / "example-release.xml",
    )
    generate_rss(
        items=[ParsedItem(title="Other item", link="/other", description="Other", pub_date=None)],
        site_name="example-other",
        site_url="https://other.example.com/",
        category=None,
        output_path=feeds_dir / "example-other.xml",
    )
    generate_rss(
        items=[ParsedItem(title="Cinema item", link="/cinema", description="Cinema", pub_date=None)],
        site_name="example-cinema",
        site_url="https://cinema.example.com/",
        category=None,
        output_path=feeds_dir / "example-cinema.xml",
    )
    # A feed left behind by an older deployment, which used to write an
    # "(unavailable)" placeholder. Generation no longer produces one, but a
    # stale copy must still be labelled honestly rather than shown as healthy.
    (feeds_dir / "example-fail.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>example-fail (unavailable)</title>"
        "<link>https://fail.example.com/</link><description>failed</description>"
        "<item><title>Feed generation failed</title>"
        "<link>https://fail.example.com/</link><description>Blocked</description>"
        "</item></channel></rss>",
        encoding="utf-8",
    )

    generate_index(
        config_path=config_path,
        feeds_dir=feeds_dir,
        output_file=output_file,
        output_opml=output_opml,
    )
    assert output_opml.is_file()  # written to the tmp dir, never the repo's feeds.opml
    html = output_file.read_text(encoding="utf-8")

    assert "<h2 class='section-title'>Movies</h2>" in html
    assert "<h2 class='section-title'>Episodes</h2>" in html
    assert "<h2 class='section-title'>Torrents</h2>" in html
    assert "<h2 class='section-title'>Cinema</h2>" in html
    assert "<h2 class='section-title'>Other</h2>" in html
    assert "feeds/example-movie.xml" in html
    assert "feeds/example-ok.xml" in html
    assert "feeds/example-other.xml" in html
    assert "feeds/example-cinema.xml" in html
    # Every enabled site gets a row, including the dead ones. A stale
    # failure-titled placeholder has a file, so its row still links to it.
    assert "feeds/example-fail.xml" in html
    # A site with no file at all is shown by name but gets no href: there is
    # nothing to link to, and a link that 404s is worse than an honest gap.
    assert "example missing" in html
    assert "feeds/example-missing.xml" not in html
    # The row is labelled by status, never by the marker baked into the feed's
    # own title -- "(unavailable)" is not the page's vocabulary.
    assert "(unavailable)" not in html
    assert "Feed generation failed" not in html
    # "releases" is an alias, not a section: it is folded into Torrents, so the
    # release feed's row lives there and Releases never gets a heading of its own.
    assert "<h2 class='section-title'>Releases</h2>" not in html
    assert "<h2 class='section-title'>Torrents</h2>" in html
    assert "example release" in html
    assert "Available" in html
    assert "Unavailable" in html
    assert "btn-inoreader" in html
    assert INOREADER_FEED_PREFIX in html
    ok_abs = f"{GITHUB_PAGES_FEED_BASE}/feeds/example-ok.xml"
    assert f"{INOREADER_FEED_PREFIX}{quote(ok_abs, safe='')}" in html
    assert "inoreader-na" in html
    opml = output_opml.read_text(encoding="utf-8")
    assert 'title="Online-Torrents"' in opml
    assert "example-release.xml" in opml
    assert 'title="Online-Movies"' in opml
    assert "example-movie.xml" in opml
    assert 'title="Online-Other"' in opml
    assert "example-other.xml" in opml
    assert 'title="Online-Cinema"' in opml
    assert "example-cinema.xml" in opml
    assert 'title="Online-Releases"' not in opml


def test_a_section_with_no_live_feeds_is_omitted_entirely(tmp_path: Path) -> None:
    """A category whose every feed is dead must not render an empty table.

    Otherwise the page grows a titled, header-only section that says nothing,
    which is the same problem as a dead row wearing a different hat.
    """
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    (tmp_path / "sites.yaml").write_text(
        "sites:\n"
        "  live:\n"
        "    display_name: Live Site\n"
        "    url: https://live.example/\n"
        "    method: rss\n"
        "    feed_file: live.xml\n"
        "    category: movies\n"
        "  deadone:\n"
        "    display_name: Dead One\n"
        "    url: https://dead1.example/\n"
        "    method: rss\n"
        "    feed_file: deadone.xml\n"
        "    category: cinema\n"
        "  deadtwo:\n"
        "    display_name: Dead Two\n"
        "    url: https://dead2.example/\n"
        "    method: rss\n"
        "    feed_file: deadtwo.xml\n"
        "    category: cinema\n",
        encoding="utf-8",
    )
    generate_rss(
        items=[ParsedItem(title="Item", link="/i", description="d", pub_date=None)],
        site_name="live",
        site_url="https://live.example/",
        category=None,
        output_path=feeds_dir / "live.xml",
    )
    output_file = tmp_path / "index.html"
    output_opml = tmp_path / "feeds.opml"

    generate_index(
        config_path=tmp_path / "sites.yaml",
        feeds_dir=feeds_dir,
        output_file=output_file,
        output_opml=output_opml,
    )
    html = output_file.read_text(encoding="utf-8")

    assert "<h2 class='section-title'>Movies</h2>" in html
    # All feeds are shown, even dead ones. The Cinema section renders with its
    # unavailable members rather than being omitted.
    assert "Cinema" in html
    assert "Dead One" in html
    assert "Dead Two" in html
    assert "Unavailable" in html
    # And never in the OPML, which is what readers actually import.
    opml = output_opml.read_text(encoding="utf-8")
    assert "live.xml" in opml
    assert "deadone.xml" not in opml
    assert "deadtwo.xml" not in opml


def _site(display_name: str, feed_file: str, category: str = "blogs") -> FeedInfo:
    site = SiteConfig(
        name=feed_file.removesuffix(".xml"),
        url="https://example.com/",
        method="rss",
        item_selector="//article",
        title_selector=".",
        link_selector=".",
        display_name=display_name,
        feed_file=feed_file,
        category=category,
    )
    return FeedInfo(
        site=site,
        href=f"feeds/{feed_file}",
        status="Available",
        last_build_date="2026-10-04 02:41 UTC",
        items_count=1,
        has_feed=True,
    )


class TestSiteLabelShortening:
    """A name is shortened only where the extra words describe the kind of site.

    Dropping the descriptive tail is what keeps the Site column narrow enough to
    sit on a shared grid. Dropping the *identifying* part -- the branch of a
    cinema, the author of a blog -- would not, and would cost the reader the one
    thing the row is for.
    """

    def test_a_description_after_a_separator_is_dropped(self) -> None:
        assert (
            _short_display_name("Securelist - Information about Viruses, Hackers and Spam")
            == "Securelist"
        )

    def test_a_generic_trailing_word_is_dropped(self) -> None:
        assert _short_display_name("Rapid7 Cybersecurity Blog") == "Rapid7"
        assert _short_display_name("gHacks Technology News") == "gHacks"
        assert _short_display_name("showRSS additions feed") == "showRSS"

    def test_a_publication_ending_in_news_keeps_the_news(self) -> None:
        """'The Hacker News' is the publication's name; 'The Hacker' is a person.

        This is the failure the class docstring warns about, and it shipped: a
        bare "news" in the noise list trimmed the name to something that reads
        as a truncation bug. "News" only describes a site as part of a compound
        ("Technology News"), where the generic adjective is already doing the
        work, so the compound is in the list and the bare word is not.
        """
        for name in ("The Hacker News", "Android News", "Bleeping Computer News"):
            assert _short_display_name(name) == name, f"{name!r} was shortened"
        # ...and the compound still shortens, or the fix went too far.
        assert _short_display_name("gHacks Technology News") == "gHacks"

    def test_the_earliest_separator_is_the_one_that_counts(self) -> None:
        """Where a name carries two separators, the leftmost one splits it.

        Cutting at the later one leaves a name that still looks like a name plus
        something -- "Foo | Bar" from "Foo | Bar - Baz" reads as two sites'
        worth of name in one cell. Order in the separator list must not decide
        this, so names with the separators in both orders are covered.
        """
        assert _short_display_name("Foo | Bar - Baz") == "Foo"
        assert _short_display_name("Alpha :: Beta - Gamma") == "Alpha"
        assert _short_display_name("ABCD - | CD") == "ABCD"

    def test_an_earliest_separator_too_close_to_the_start_is_not_used(self) -> None:
        """The leftmost separator still has to leave a name behind.

        "A :: B - C" splits earliest at "::", but "A" is not a site, so the
        whole name stands. Falling back to the later "-" would give "A :: B",
        which is longer and no more a name than what it started as.
        """
        for name in ("A :: B - C", "AB - | CD"):
            assert _short_display_name(name) == name

    def test_the_tail_is_stripped_repeatedly_not_once(self) -> None:
        """'Google Online Security Blog' sheds 'Security Blog' and still has
        'Online' on the end, so a single pass would leave 'Google Online'."""
        assert _short_display_name("Google Online Security Blog") == "Google"

    def test_words_that_identify_the_site_are_kept(self) -> None:
        # The Cinema City branches differ only by the words kept here, and
        # "Cinema" is the word the noise list must never touch.
        for name in (
            "Cinema City Afi Cotroceni",
            "Cinema City Mega Mall",
            "Cinema City ParkLake",
            "Happy Cinema Colosseum",
            "Movieplex Cinema Bucuresti",
        ):
            assert _short_display_name(name) == name

    def test_a_name_is_never_shortened_to_nothing(self) -> None:
        """The noise list holds words a site can be entirely made of.

        Reducing 'Online' or 'Blog' to an empty cell would render a row with no
        label at all, so anything leaving too little behind is left alone.
        """
        for name in ("Online", "Blog", "News", "Feed"):
            assert _short_display_name(name) == name

    def test_a_separator_too_close_to_the_start_is_not_a_separator(self) -> None:
        assert _short_display_name("a - b") == "a - b"

    def test_a_collision_does_not_steal_the_shorter_sites_name(self) -> None:
        """Two sites can shorten to one label; only one may keep it.

        The site that gave up nothing claims it first, so a site actually named
        'Foo' is not displaced by 'Foo Blog' trimmed down to meet it.
        """
        rows = _labelled_rows([_site("Foo Blog", "alpha.xml"), _site("Foo", "beta.xml")], "Blogs")

        assert [(feed.site.feed_file, label) for feed, label, _full in rows] == [
            ("beta.xml", "Foo"),
            ("alpha.xml", "Foo Blog"),
        ]

    def test_collapsing_whitespace_is_not_treated_as_shortening(self) -> None:
        """A doubled space is tidied, not trimmed, and must not look trimmed.

        The collision fallback promotes a site's label back to its full name
        when another site already claims the shortened one. If a name that was
        merely whitespace-collapsed counted as shortened, this site would be
        pushed through that fallback and end up labelled with its own raw,
        ragged display_name -- the precise outcome the fallback exists to avoid,
        handed out for a name nobody shortened.
        """
        rows = _labelled_rows([_site("Foo  Bar", "alpha.xml"), _site("Foo Bar", "beta.xml")], "Blogs")

        labels = {feed.site.feed_file: label for feed, label, _full in rows}
        assert labels["alpha.xml"] == "Foo Bar", "collapsed whitespace was mistaken for a trim"
        assert labels["beta.xml"] == "Foo Bar"

    def test_a_name_of_punctuation_falls_back_to_the_site_key(self) -> None:
        """A cell has to hold something that identifies the site.

        display_name is free text and load_config does not police it, so it can
        arrive blank or as bare separator debris, and shortening cannot help:
        there is no name inside "-" to find. The config key is the only label
        left that says anything, and beats a blank cell or a lone hyphen.
        """
        for name in ("-", " | ", "::", "   ", ""):
            rows = _labelled_rows([_site(name, "alpha.xml")], "Blogs")
            _feed, label, _full = rows[0]
            assert label.strip(), f"{name!r} produced an empty cell"
            assert any(ch.isalnum() for ch in label), f"{name!r} produced the label {label!r}"
            assert label == "alpha", f"{name!r} fell back to {label!r}, not the site key"

    def test_the_row_renderer_is_given_both_labels(self) -> None:
        """It derives neither, because deriving the full name needs the section.

        _site_display_name strips a redundant category suffix only when it knows
        the section title, so a fallback that rebuilt the name without it would
        label "Xfilme.ro Episodes" as "Xfilme.ro Episodes" under a column headed
        "Episodes". Required arguments make that unrepresentable instead of
        merely unlikely.
        """
        import inspect

        signature = inspect.signature(_feed_row_lines)
        assert signature.parameters["label"].default is inspect.Parameter.empty
        assert signature.parameters["full_label"].default is inspect.Parameter.empty

    def test_the_full_name_stays_on_every_row_as_a_tooltip(self, tmp_path: Path) -> None:
        """The tooltip is unconditional rather than only for shortened names.

        Which names overflow the fixed-width column is a property of the font
        and the viewport, so deciding in Python which ones need it would be a
        guess that goes stale silently. The cost of always adding it is one
        attribute that repeats the visible text.
        """
        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        (tmp_path / "sites.yaml").write_text(
            "sites:\n"
            "  loud:\n"
            "    display_name: Securelist - Information about Viruses, Hackers and Spam\n"
            "    url: https://securelist.example/\n"
            "    method: rss\n"
            "    feed_file: loud.xml\n"
            "    category: cyber\n"
            "  quiet:\n"
            "    display_name: B365\n"
            "    url: https://b365.example/\n"
            "    method: rss\n"
            "    feed_file: quiet.xml\n"
            "    category: cyber\n",
            encoding="utf-8",
        )
        for feed_file in ("loud.xml", "quiet.xml"):
            generate_rss(
                items=[ParsedItem(title="Item", link="/i", description="d", pub_date=None)],
                site_name=feed_file.removesuffix(".xml"),
                site_url="https://example.com/",
                category=None,
                output_path=feeds_dir / feed_file,
            )
        output_file = tmp_path / "index.html"

        generate_index(
            config_path=tmp_path / "sites.yaml",
            feeds_dir=feeds_dir,
            output_file=output_file,
            output_opml=tmp_path / "feeds.opml",
        )
        html = output_file.read_text(encoding="utf-8")

        assert (
            "<td class='col-site' "
            "title='Securelist - Information about Viruses, Hackers and Spam'>Securelist</td>" in html
        )
        # Even the row that lost nothing carries the name.
        assert "<td class='col-site' title='B365'>B365</td>" in html

    def test_shortening_stays_out_of_the_opml_the_page_shares_its_source_with(self, tmp_path: Path) -> None:
        """The page and the OPML both read `display_name`, and must not agree.

        This is the guard against "fixing" the column width by shortening the
        names in `config/sites.yaml`: the day someone does, the OPML starts
        labelling subscriptions 'Securelist' instead of the full name and every
        existing subscriber sees their feed renamed. The index page and the OPML
        are generated from one source in one pass, so a shortening that reached
        only the OPML would show up as this pair of assertions disagreeing.
        """
        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        full = "Securelist - Information about Viruses, Hackers and Spam"
        (tmp_path / "sites.yaml").write_text(
            "sites:\n"
            "  loud:\n"
            f"    display_name: {full}\n"
            "    url: https://securelist.example/\n"
            "    method: rss\n"
            "    feed_file: loud.xml\n"
            "    category: cyber\n",
            encoding="utf-8",
        )
        generate_rss(
            items=[ParsedItem(title="Item", link="/i", description="d", pub_date=None)],
            site_name=full,
            site_url="https://securelist.example/",
            category=None,
            output_path=feeds_dir / "loud.xml",
        )
        before = (feeds_dir / "loud.xml").read_bytes()
        output_file = tmp_path / "index.html"
        output_opml = tmp_path / "feeds.opml"

        generate_index(
            config_path=tmp_path / "sites.yaml",
            feeds_dir=feeds_dir,
            output_file=output_file,
            output_opml=output_opml,
        )
        html = output_file.read_text(encoding="utf-8")
        opml = output_opml.read_text(encoding="utf-8")

        assert f"title='{full}'>Securelist</td>" in html, "the page should show the short label"
        assert f'title="{full}"' in opml, "the OPML must keep the full name"
        # And the index pass only ever reads a feed; it never rewrites one.
        assert (feeds_dir / "loud.xml").read_bytes() == before


def _css_rule(html: str, selector: str) -> str:
    """The declarations of the rule for `selector`, or "" if there is no such rule.

    Scoping the lookup to the selector is the entire point. A document-wide
    search for "text-overflow: ellipsis" is satisfied just as happily when the
    declaration has been moved onto some unrelated rule, or when a later rule
    overrides it back, and neither is a truncation any more.

    Comments come out first: the rules here are commented, and a comment between
    the previous rule and this one is otherwise parsed as part of this selector,
    which is a selector nothing matches.
    """
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", html):
        if selector in (part.strip() for part in selectors.split(",")):
            return declarations
    return ""


class TestSectionGrid:
    """Every category table has to be the same grid as every other one.

    The page is a stack of independent tables, so nothing forces their columns
    to line up: without an explicit shared layout each sizes its columns to its
    own contents, and the grid visibly shifts as you scroll from a section of
    four-character names to one carrying a 56-character name.
    """

    @staticmethod
    def _render(tmp_path: Path, rows: list[tuple[str, str, str]]) -> str:
        """Generate an index for `rows` of (slug, display_name, category)."""
        feeds_dir = tmp_path / "feeds"
        feeds_dir.mkdir()
        # safe_dump, not f-strings: a display_name is scraped text, and one
        # containing ": " or a leading "#" silently parses as something else,
        # which would make these tests describe a different site than intended.
        config = yaml.safe_dump(
            {
                "sites": {
                    slug: {
                        "display_name": display,
                        "url": "https://example.com/",
                        "method": "rss",
                        "feed_file": f"{slug}.xml",
                        "category": category,
                    }
                    for slug, display, category in rows
                }
            },
            sort_keys=False,
        )
        (tmp_path / "sites.yaml").write_text(config, encoding="utf-8")
        for slug, _display, _category in rows:
            generate_rss(
                items=[ParsedItem(title="Item", link="/i", description="d", pub_date=None)],
                site_name=slug,
                site_url="https://example.com/",
                category=None,
                output_path=feeds_dir / f"{slug}.xml",
            )
        output_file = tmp_path / "index.html"
        generate_index(
            config_path=tmp_path / "sites.yaml",
            feeds_dir=feeds_dir,
            output_file=output_file,
            output_opml=tmp_path / "feeds.opml",
        )
        return output_file.read_text(encoding="utf-8")

    def test_every_section_shares_one_colgroup(self, tmp_path: Path) -> None:
        html = self._render(
            tmp_path,
            [
                ("movies-one", "A", "movies"),
                ("cinema-one", "Cinema City Afi Cotroceni", "cinema"),
                ("cyber-one", "Securelist - Information about Viruses, Hackers and Spam", "cyber"),
            ],
        )
        grids = re.findall(r"<colgroup>(.*?)</colgroup>", html, flags=re.S)

        assert len(grids) == 3, "expected a table per category"
        assert len({grid for grid in grids}) == 1, (
            f"the sections were given different column widths, so the grid shifts between them: {grids}"
        )

    def test_the_columns_are_fixed_rather_than_content_sized(self, tmp_path: Path) -> None:
        """A colgroup is only binding under `table-layout: fixed`.

        Under the default auto layout the browser treats those percentages as a
        suggestion and hands the leftover width to whichever column has the
        longest content, which is the original problem.
        """
        html = self._render(tmp_path, [("one", "One", "movies")])

        assert "table-layout: fixed" in _css_rule(html, "table")

    def test_a_long_name_is_truncated_rather_than_wrapped(self, tmp_path: Path) -> None:
        """Truncation, not wrapping.

        A name allowed to wrap makes its row two lines tall while its neighbours
        stay one, which is the ragged look the grid exists to remove -- and it
        would happen only to the rows that can least afford it. All three
        declarations are needed: nowrap stops the break, overflow lets the
        ellipsis appear at all, and the ellipsis is what tells the reader the
        cell is cut rather than simply short.
        """
        html = self._render(tmp_path, [("one", "Securelist - Information about Viruses", "movies")])

        rule = _css_rule(html, "td.col-site")
        assert rule, "td.col-site has no rule, so names are neither truncated nor styled"
        for declaration in ("white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis"):
            assert declaration in rule, f"td.col-site is missing {declaration!r}, so: {rule!r}"

    def test_the_percentages_total_one_hundred(self) -> None:
        """A colgroup that does not add up is not a set of shares.

        Under fixed layout the browser takes these as widths regardless of what
        they sum to, so a 102 total silently narrows the table inside its own
        box and a 98 one leaves a gap. Nothing warns about either.
        """
        assert sum(percent for _name, percent in _COLUMN_WIDTHS) == 100

    def test_no_column_is_narrower_than_its_widest_text(self) -> None:
        """The check that the guessed percentages failed, and the only one that matters.

        Fixed layout cannot grow a column to fit, so a column allocated less
        than its widest value does not reflow the text: it paints the overflow
        over the next cell. Two adjacent cells share their padding, so text
        reaches its neighbour's first glyph once it exceeds the whole cell box.

        The floor is the binding case, because above it every column's share
        grows with the table. The needs themselves are measured in Chromium and
        live beside the percentages in the module, so content that outgrows them
        fails here instead of turning into overlapping text nobody checks for.
        """
        short = [
            f"{name}: {_TABLE_MIN_WIDTH * percent / 100:.1f}px allocated, "
            f"{_MEASURED_COLUMN_NEEDS[name]}px needed"
            for name, percent in _COLUMN_WIDTHS
            if _TABLE_MIN_WIDTH * percent / 100 < _MEASURED_COLUMN_NEEDS[name]
        ]

        assert not short, "these columns paint over their neighbour: " + "; ".join(short)

    def test_every_cell_has_one_entry_per_column(self, tmp_path: Path) -> None:
        """A col, a header and a cell per column, or the grid is a fiction.

        The <col>s and the <th>s are generated from the same tuple so they
        cannot disagree with each other, but the <td>s are written out by hand,
        so adding a column to _COLUMN_WIDTHS would leave every row one cell short
        and shift its values left by one -- which looks like data, not a bug.
        """
        html = self._render(tmp_path, [("one", "One", "movies")])

        cols = re.findall(r"<colgroup>(.*?)</colgroup>", html, flags=re.S)[0]
        expected = len(_COLUMN_WIDTHS)
        assert len(re.findall(r"<col\b", cols)) == expected
        for row in re.findall(r"<tr>(.*?)</tr>", html, flags=re.S):
            n = len(re.findall(r"<t[dh]\b", row))
            assert n == expected, f"a row has {n} cells for {expected} columns: {row!r}"


class TestShippedLabels:
    """The labels the real config/sites.yaml produces, pinned one by one.

    Shortening a name is a judgement call, and no rule derives the right answer
    from the wrong one. "The Hacker News" losing its "News" leaves "The Hacker",
    which is a person who breaks into things: a label that reads as a truncation
    bug rather than as a name, and one that no assertion in this file would have
    noticed. So the decisions are pinned as data. Adding a site to
    config/sites.yaml, or renaming one, changes this map and fails, which is the
    point -- a new label is a new thing for somebody to read, and it should
    arrive with a human having read it.

    Derived from the config alone (the source of truth) rather than from a
    generated page, so the map exists even before any feed has been built and
    cannot silently shrink to the subset a particular run happened to produce.
    """

    @staticmethod
    def _shipped_labels() -> dict[str, str]:
        from core.config import load_config

        titles = {category: title for category, title, _folder in _CATEGORY_SECTIONS}
        config = load_config(CONFIG_FILE)
        labels: dict[str, str] = {}
        for site in config.sites:
            if not site.enabled:
                continue
            category = _CATEGORY_GROUP_ALIASES.get(site.category, site.category)
            title = titles.get(category, "")
            full = _site_display_name(site, title)
            label = _short_display_name(full)
            labels[full] = label if label and any(ch.isalnum() for ch in label) else full
        return labels

    def test_the_shipped_names_shorten_to_these_labels(self) -> None:
        expected = {
            "DoublePulsar - Medium": "DoublePulsar",
            "Malwarebytes Unpacked": "Malwarebytes",
            "Rapid7 Cybersecurity Blog": "Rapid7",
            "gHacks Technology News": "gHacks",
            "mihai vasilescu blog": "mihai vasilescu",
            "nwradu blog": "nwradu",
            "showRSS additions feed": "showRSS",
            "Securelist - Information about Viruses, Hackers and Spam": "Securelist",
        }
        shipped = self._shipped_labels()
        changed = {name: label for name, label in shipped.items() if name != label}

        assert changed == expected, (
            "the set of shortened site labels has changed. Each one needs reading: "
            "a new entry here is a new label on the page, and a removed one is a "
            "site that now shows its full name in a narrower column."
        )
