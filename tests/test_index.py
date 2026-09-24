from pathlib import Path
from urllib.parse import quote

from core.feed import generate_failure_rss, generate_rss
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
    generate_failure_rss(
        site_name="example-fail",
        site_url="https://fail.example.com/",
        output_path=feeds_dir / "example-fail.xml",
        error_message="Blocked by upstream",
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
    assert "<h2 class='section-title'>Releases</h2>" in html
    assert "<h2 class='section-title'>Cinema</h2>" in html
    assert "<h2 class='section-title'>Other</h2>" in html
    assert "feeds/example-ok.xml" in html
    assert "feeds/example-other.xml" in html
    assert "feeds/example-release.xml" in html
    assert "feeds/example-cinema.xml" in html
    assert "feeds/example-fail.xml" in html
    assert "Available" in html
    assert "Unavailable" in html
    assert "Missing" in html
    assert "btn-inoreader" in html
    assert INOREADER_FEED_PREFIX in html
    ok_abs = f"{GITHUB_PAGES_FEED_BASE}/feeds/example-ok.xml"
    assert f"{INOREADER_FEED_PREFIX}{quote(ok_abs, safe='')}" in html
    assert "inoreader-na" in html
    opml = output_opml.read_text(encoding="utf-8")
    assert 'title="Online-Releases"' in opml
    assert "example-release.xml" in opml
    assert 'title="Online-Other"' in opml
    assert "example-other.xml" in opml
    assert 'title="Online-Cinema"' in opml
    assert "example-cinema.xml" in opml
    assert 'title="Online-Torrents"' not in opml
