import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

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
    assert channel.findtext("item/title") == "Item 1"


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
    assert channel.findtext("item/title") == "Feed generation failed"
    assert "All fetch candidates failed for example" in (channel.findtext("description") or "")
