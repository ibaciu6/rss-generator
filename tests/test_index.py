from pathlib import Path
from urllib.parse import quote

from core.feed import generate_rss
from scraper.parser import ParsedItem
from scripts.generate_index import GITHUB_PAGES_FEED_BASE, INOREADER_FEED_PREFIX, generate_index


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
    # A deleted feed and a stale failure-titled placeholder are catalogued
    # nowhere. Both are dead: one 404s, the other advertises itself as broken.
    # The Feed Health dashboard still counts them, so nothing is hidden.
    assert "feeds/example-fail.xml" not in html
    assert "feeds/example-missing.xml" not in html
    assert "(unavailable)" not in html
    assert "Feed generation failed" not in html
    # The whole Releases section disappears with its only (unavailable) member,
    # rather than rendering an empty table.
    assert "<h2 class='section-title'>Releases</h2>" not in html
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
    assert "Cinema" not in html
    assert "Dead One" not in html
    assert "Dead Two" not in html
    # Still reported as unhealthy, just not catalogued.
    assert "Unavailable" in html
    # And never in the OPML, which is what readers actually import.
    opml = output_opml.read_text(encoding="utf-8")
    assert "live.xml" in opml
    assert "deadone.xml" not in opml
    assert "deadtwo.xml" not in opml
