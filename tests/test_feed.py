import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.feed import generate_failure_rss, generate_rss
from scraper.parser import ParsedItem


def test_generate_rss(tmp_path: Path) -> None:
    items = [
        ParsedItem(
            title="Item 1",
            link="https://example.com/1",
            description="Desc 1",
            pub_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    ]
    output = tmp_path / "feed.xml"

    generate_rss(
        items,
        site_name="example",
        site_url="https://example.com/",
        category="test",
        output_path=output,
    )

    root = ET.parse(output).getroot()
    channel = root.find("channel")

    assert output.exists()
    assert channel is not None
    assert channel.findtext("title") == "example"
    assert channel.findtext("link") == "https://example.com/"
    assert channel.findtext("ttl") == "60"
    assert channel.findtext("pubDate")
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updatePeriod") == "hourly"
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updateFrequency") == "1"
    atom_links = channel.findall("{http://www.w3.org/2005/Atom}link")
    self_link = next((l for l in atom_links if l.attrib.get("rel") == "self"), None)
    hub_link = next((l for l in atom_links if l.attrib.get("rel") == "hub"), None)
    assert self_link is not None
    assert self_link.attrib["href"] == "feed.xml"
    assert hub_link is not None
    assert hub_link.attrib["href"] == "https://pubsubhubbub.appspot.com/"
    assert channel.findtext("item/title") == "Item 1"


def test_generate_rss_enforces_poster_img_bounds_and_tmdb_size(tmp_path: Path) -> None:
    """Poster images get max 300×450 style; TMDB paths are downscaled to w342."""
    desc = (
        '<img src="https://image.tmdb.org/t/p/w780/foo.jpg" width="800" height="1200" '
        'style="max-width:999px;">'
        '<br><a href="https://example.com">link</a>'
    )
    out = tmp_path / "feed.xml"
    generate_rss(
        [
            ParsedItem(
                title="T",
                link="https://example.com/p",
                description=desc,
                pub_date=None,
            )
        ],
        site_name="example",
        site_url="https://example.com/",
        category=None,
        output_path=out,
    )
    item_desc = out.read_text(encoding="utf-8")
    assert "w342/foo.jpg" in item_desc
    assert "w780" not in item_desc
    assert "max-width:300px" in item_desc
    assert "max-height:450px" in item_desc
    assert 'width="800"' not in item_desc
    assert 'height="1200"' not in item_desc
    assert "999px" not in item_desc
    assert "object-fit:contain" in item_desc


def test_generate_rss_self_link_absolute_when_public_base_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    out = feeds / "x.xml"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RSS_FEED_PUBLIC_BASE", "https://example.com/site")

    generate_rss(
        [
            ParsedItem(
                title="One",
                link="/p/1",
                description=None,
                pub_date=None,
            )
        ],
        site_name="example",
        site_url="https://example.com/",
        category=None,
        output_path=out,
    )

    channel = ET.parse(out).getroot().find("channel")
    assert channel is not None
    atom_link = channel.find("{http://www.w3.org/2005/Atom}link")
    assert atom_link is not None
    assert atom_link.attrib["href"] == "https://example.com/site/feeds/x.xml"


def test_generate_failure_rss(tmp_path: Path) -> None:
    output = tmp_path / "feed.xml"

    generate_failure_rss(
        site_name="example",
        site_url="https://example.com/",
        output_path=output,
        error_message="All fetch candidates failed for example",
    )

    root = ET.parse(output).getroot()
    channel = root.find("channel")

    assert output.exists()
    assert channel is not None
    assert channel.findtext("title") == "example (unavailable)"
    assert channel.findtext("link") == "https://example.com/"
    assert channel.findtext("ttl") == "60"
    assert channel.findtext("pubDate")
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updatePeriod") == "hourly"
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updateFrequency") == "1"
    assert channel.findtext("item/title") == "Feed generation failed"
    assert "All fetch candidates failed for example" in (channel.findtext("description") or "")
    atom_links = channel.findall("{http://www.w3.org/2005/Atom}link")
    hub_link = next((l for l in atom_links if l.attrib.get("rel") == "hub"), None)
    assert hub_link is not None
    assert hub_link.attrib["href"] == "https://pubsubhubbub.appspot.com/"


def test_generate_failure_rss_sanitizes_playwright_call_log(tmp_path: Path) -> None:
    """Multi-line Playwright call logs should not leak into the feed description."""

    output = tmp_path / "feed.xml"
    noisy = (
        "HTML scrape failed: Page.goto: Timeout 20000ms exceeded.\n"
        "Call log:\n"
        "  - navigating to \"https://example.com/\", waiting until \"load\"\n"
        "\n"
        "; cloudscraper: connection reset"
    )
    generate_failure_rss(
        site_name="example",
        site_url="https://example.com/",
        output_path=output,
        error_message=noisy,
    )
    description = ET.parse(output).getroot().find("channel").findtext("description")
    assert description is not None
    assert "Call log:" not in description
    assert "navigating to" not in description
    assert "HTML scrape failed" in description
    assert "cloudscraper" in description
    # Single-line description (readers collapse whitespace anyway, but we
    # guarantee no embedded newlines survive).
    assert "\n" not in description
