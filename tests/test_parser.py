from unittest.mock import patch
import json
from scraper.parser import Parser

def test_tvmaze_schedule_poster_url_is_escaped():
    parser = Parser()
    payload = json.dumps([
        {
            "season": 1,
            "number": 2,
            "name": "Ep",
            "airdate": "2026-11-04",
            "url": "https://tvmaze.com/ep",
            "show": {
                "name": "Show",
                "url": "https://tvmaze.com/show",
                "image": {"medium": 'https://example.com/img.jpg" onload="alert(1)'},
            }
        }
    ])
    items = parser.parse_tvmaze_schedule(payload)
    assert len(items) == 1
    item = items[0]
    assert item.description is not None
    assert '" onload="alert(1)' not in item.description
    assert 'https://example.com/img.jpg"%20onload="alert(1)' in item.description

def test_parse_tvmaze_schedule_skips_missing_url():
    # ... existing ...