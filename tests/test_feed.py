from datetime import datetime, timezone
from pathlib import Path

from core.feed import generate_rss_and_atom
from scraper.parser import ParsedItem


def test_generate_rss_and_atom(tmp_path: Path) -> None:
    items = [
        ParsedItem(
            title="Item 1",
            link="https://example.com/1",
            description="Desc 1",
            pub_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    ]
    output = tmp_path / "feed.xml"

    generate_rss_and_atom(
        items,
        site_name="example",
        site_url="https://example.com/",
        category="test",
        output_path=output,
    )

    assert output.exists()
    assert output.with_suffix(".atom.xml").exists()

