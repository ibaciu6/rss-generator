from pathlib import Path
from urllib.parse import quote

from core.feed import generate_failure_rss, generate_rss
from scraper.parser import ParsedItem
from scripts.generate_index import GITHUB_PAGES_FEED_BASE, INOREADER_FEED_PREFIX, generate_index


def test_generate_static_index_lists_sources_without_status(tmp_path: Path) -> None:
    config_dir = tmp_path / "sites"
    feeds_dir = tmp_path / "feeds"
    output_file = tmp_path / "index.html"
    config_dir.mkdir()
    feeds_dir.mkdir()

    (config_dir / "example-ok.yaml").write_text(
        """
url: "https://example.com/"
method: "http"
item_selector: "//article"
title_selector: ".//h2/text()"
link_selector: ".//a/@href"
feed_file: "example-ok.xml"
category: "movies"
""",
        encoding="utf-8",
    )
    (config_dir / "example-fail.yaml").write_text(
        """
url: "https://fail.example.com/"
method: "http"
item_selector: "//article"
title_selector: ".//h2/text()"
link_selector: ".//a/@href"
feed_file: "example-fail.xml"
category: "episodes"
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
    generate_failure_rss(
        site_name="example-fail",
        site_url="https://fail.example.com/",
        output_path=feeds_dir / "example-fail.xml",
        error_message="Blocked by upstream",
    )

    generate_index(config_path=config_dir, feeds_dir=feeds_dir, output_file=output_file)
    html = output_file.read_text(encoding="utf-8")

    assert "<h2 class='section-title'>Filme</h2>" in html
    assert "<h2 class='section-title'>Seriale</h2>" in html
    assert "feeds/example-ok.xml" in html
    assert "feeds/example-fail.xml" in html
    assert "btn-inoreader" in html
    assert INOREADER_FEED_PREFIX in html
    ok_abs = f"{GITHUB_PAGES_FEED_BASE}/feeds/example-ok.xml"
    assert f"{INOREADER_FEED_PREFIX}{quote(ok_abs, safe='')}" in html
    assert "static" in html.lower() or "config/sites" in html
    assert "<th>Status</th>" not in html
    assert "<th>Items</th>" not in html
