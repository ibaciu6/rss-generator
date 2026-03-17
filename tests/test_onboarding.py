from __future__ import annotations

from pathlib import Path

from core.config import SiteConfig
from core.onboarding import (
    FetchSnapshot,
    PreviewOption,
    append_site_config,
    derive_site_slug,
    write_preview_feeds,
    _discover_options_from_snapshot,
)
from scraper.parser import ParsedItem, Parser


def test_derive_site_slug_strips_common_prefixes() -> None:
    assert derive_site_slug("https://www3.fsonline.app/") == "fsonline"
    assert derive_site_slug("portalultautv.info") == "portalultautv"


def test_discover_options_from_snapshot_finds_repeated_cards() -> None:
    html = """
    <html>
      <body>
        <section class="listing">
          <article class="card item">
            <a href="/one">
              <img src="https://img.example/one.jpg" alt="One poster">
              <h2>One</h2>
            </a>
            <p>First summary</p>
          </article>
          <article class="card item">
            <a href="/two">
              <img src="https://img.example/two.jpg" alt="Two poster">
              <h2>Two</h2>
            </a>
            <p>Second summary</p>
          </article>
          <article class="card item">
            <a href="/three">
              <img src="https://img.example/three.jpg" alt="Three poster">
              <h2>Three</h2>
            </a>
            <p>Third summary</p>
          </article>
        </section>
      </body>
    </html>
    """
    snapshot = FetchSnapshot(
        method="cloudscraper",
        final_url="https://example.com/",
        content=html,
        page_title="Example",
    )

    options = _discover_options_from_snapshot(snapshot, parser=Parser())

    assert options
    assert any(option.style_name == "basic" for option in options)
    assert any(option.description_selector for option in options)
    assert options[0].item_count >= 3
    assert options[0].preview_items[0].link.startswith("https://example.com/")


def test_append_site_config_writes_new_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "sites.yaml"
    config_path.write_text("sites:\n  existing:\n    url: https://example.com/\n    method: http\n    item_selector: //article\n    title_selector: .//h2/text()\n    link_selector: .//a/@href\n", encoding="utf-8")

    append_site_config(
        config_path,
        SiteConfig(
            name="new-site",
            display_name="New Site",
            url="https://new.example/",
            method="cloudscraper",
            item_selector="//article[contains(@class,'item')]",
            title_selector=".//h2/text()",
            link_selector=".//a/@href",
            description_selector="concat('<p>', .//p[1], '</p>')",
            feed_file="new-site.xml",
            category="updates",
            max_items=24,
        ),
    )

    content = config_path.read_text(encoding="utf-8")
    assert "new-site:" in content
    assert "cloudscraper" in content
    assert "new-site.xml" in content


def test_write_preview_feeds_generates_files(tmp_path: Path) -> None:
    preview_dir = tmp_path / "previews"
    options = [
        PreviewOption(
            fetch_method="http",
            final_url="https://example.com/",
            item_selector="//article",
            title_selector=".//h2",
            link_selector=".//a/@href",
            description_selector=None,
            style_name="basic",
            item_count=3,
            preview_items=(
                ParsedItem(title="One", link="https://example.com/one", description=None, pub_date=None),
                ParsedItem(title="Two", link="https://example.com/two", description=None, pub_date=None),
            ),
            score=10.0,
            preview_path=None,
        )
    ]

    written = write_preview_feeds(options, preview_dir, "https://example.com/")

    assert written[0].preview_path is not None
    assert written[0].preview_path.exists()
