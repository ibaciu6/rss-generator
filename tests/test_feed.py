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
    assert channel.findtext("ttl") == "30"
    assert channel.findtext("pubDate")
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updatePeriod") == "hourly"
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updateFrequency") == "1"
    atom_link = channel.find("{http://www.w3.org/2005/Atom}link")
    assert atom_link is not None
    assert atom_link.attrib["href"] == "feed.xml"
    assert channel.findtext("item/title") == "Item 1"


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
    assert channel.findtext("ttl") == "30"
    assert channel.findtext("pubDate")
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updatePeriod") == "hourly"
    assert channel.findtext("{http://purl.org/rss/1.0/modules/syndication/}updateFrequency") == "1"
    assert channel.findtext("item/title") == "Feed generation failed"
    assert "All fetch candidates failed for example" in (channel.findtext("description") or "")
