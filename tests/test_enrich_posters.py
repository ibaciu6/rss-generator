from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.enrich_posters import (
    FEEDS_DIR,
    IMG_TAG_RE,
    _extract_tmdb_id,
    _title_matches,
    process_feed,
)
from core.tmdb import MovieInfo


def _make_feed(
    items: list[dict],
    title: str = "Test Feed",
    link: str = "https://example.com/",
) -> Path:
    path = FEEDS_DIR / "test-enrich-tmp.xml"
    path.parent.mkdir(parents=True, exist_ok=True)

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title + " (test)"
    ET.SubElement(channel, "link").text = link

    for item in items:
        item_el = ET.SubElement(channel, "item")
        ET.SubElement(item_el, "title").text = item.get("title", "Test Movie (2024)")
        ET.SubElement(item_el, "link").text = item.get("link", "https://example.com/movie/12345")
        desc_text = item.get("description")
        if desc_text is not None:
            desc = ET.SubElement(item_el, "description")
            desc.text = desc_text

    tree = ET.ElementTree(rss)
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return path


def _cleanup():
    p = FEEDS_DIR / "test-enrich-tmp.xml"
    if p.exists():
        p.unlink()


def _read_item_desc(path: Path, idx: int = 0) -> str | None:
    tree = ET.parse(path)
    items = tree.findall(".//item")
    if idx < len(items):
        d = items[idx].find("description")
        return d.text if d is not None else None
    return None


def _read_item_title(path: Path, idx: int = 0) -> str | None:
    tree = ET.parse(path)
    items = tree.findall(".//item")
    if idx < len(items):
        t = items[idx].find("title")
        return t.text if t is not None else None
    return None


# --- Unit tests ---

class TestExtractTmdbId:
    def test_basic_movie(self):
        assert _extract_tmdb_id("https://cinezo.net/movie/1318447") == ("movie", 1318447)

    def test_movie_with_slug(self):
        assert _extract_tmdb_id("https://cinezo.net/movie/the-mummy-2026/1304313") == ("movie", 1304313)

    def test_tv(self):
        assert _extract_tmdb_id("https://example.com/tv/12345") == ("tv", 12345)

    def test_no_match(self):
        assert _extract_tmdb_id("https://example.com/page/123") is None

    def test_short_id(self):
        assert _extract_tmdb_id("https://example.com/movie/123") is None


class TestTitleMatching:
    def test_exact_match(self):
        assert _title_matches("The Mummy", "The Mummy (2026)")

    def test_partial_word_match(self):
        assert _title_matches("Mummy", "The Mummy (2026)")

    def test_completely_different(self):
        assert not _title_matches("Star Wars", "The Mummy (2026)")

    def test_empty_handling(self):
        assert _title_matches("", "The Mummy (2026)")
        assert _title_matches("The Mummy", "")
        assert _title_matches("", "")

    def test_year_in_title(self):
        assert _title_matches("The Devil Wears Prada", "The Devil Wears Prada (2006)")


class TestProcessFeed:
    """End-to-end tests of process_feed() with mocked TMDB lookups."""

    @staticmethod
    def _mock_lookup(tmdb_id: int) -> MovieInfo:
        if tmdb_id == 12345:
            return MovieInfo(
                poster_url="https://image.tmdb.org/t/p/w342/poster1.jpg",
                year="2024",
                title="Test Movie",
                release_date="2024-06-15",
            )
        if tmdb_id == 67890:
            return MovieInfo(
                poster_url="https://image.tmdb.org/t/p/w342/poster2.jpg",
                year="2026",
                title="Future Movie",
                release_date=str(date.today() + timedelta(days=30)),
            )
        return MovieInfo()

    @staticmethod
    def _mock_search(title: str, year: str | None = None) -> MovieInfo:
        if "found" in title.lower():
            return MovieInfo(
                poster_url="https://image.tmdb.org/t/p/w342/search.jpg",
                year="2025",
                title=title,
            )
        return MovieInfo()

    @patch("scripts.enrich_posters.movie_lookup")
    def test_inserts_poster_when_no_img(self, mock_lookup):
        mock_lookup.side_effect = self._mock_lookup
        path = _make_feed([
            {
                "title": "Test Movie (2024)",
                "link": "https://example.com/movie/12345",
                "description": '<a href="https://youtube.com">Trailer</a>',
            }
        ])
        try:
            changed, stats = process_feed(path)
            assert changed
            assert stats["posters"] == 1
            assert stats["items"] == 1

            desc = _read_item_desc(path)
            assert desc is not None
            assert IMG_TAG_RE.search(desc), "img tag should exist"
            assert 'src="https://image.tmdb.org/t/p/w342/poster1.jpg"' in desc
            assert "<br>" in desc
            assert "Trailer" in desc
        finally:
            _cleanup()

    @patch("scripts.enrich_posters.movie_lookup")
    def test_skips_complete_item(self, mock_lookup):
        """Items with year + img already present are left unchanged."""
        mock_lookup.side_effect = self._mock_lookup
        path = _make_feed([
            {
                "title": "Test Movie (2024)",
                "link": "https://example.com/movie/12345",
                "description": '<img src="https://old.com/poster.jpg"><a href="https://youtube.com">Trailer</a>',
            }
        ])
        try:
            changed, stats = process_feed(path)
            # Already has year + img, so no change needed
            assert not changed
            assert stats["posters"] == 0
            assert stats["items"] == 1
        finally:
            _cleanup()

    @patch("scripts.enrich_posters.movie_lookup")
    def test_filters_future_items(self, mock_lookup):
        mock_lookup.side_effect = self._mock_lookup
        path = _make_feed([
            {
                "title": "Future Movie (2026)",
                "link": "https://example.com/movie/67890",
                "description": "Some description",
            },
            {
                "title": "Test Movie (2024)",
                "link": "https://example.com/movie/12345",
                "description": "Another desc",
            },
        ])
        try:
            changed, stats = process_feed(path)
            assert changed
            assert stats["future"] == 1
            assert stats["items"] == 1

            tree = ET.parse(path)
            titles = [i.findtext("title") for i in tree.findall(".//item")]
            assert "Future Movie (2026)" not in titles
            assert "Test Movie (2024)" in titles
        finally:
            _cleanup()

    @patch("scripts.enrich_posters.movie_lookup")
    def test_adds_year_to_title(self, mock_lookup):
        mock_lookup.side_effect = self._mock_lookup
        path = _make_feed([
            {
                "title": "Test Movie",
                "link": "https://example.com/movie/12345",
                "description": "Desc without year",
            }
        ])
        try:
            changed, stats = process_feed(path)
            assert changed
            assert stats["years"] == 1

            title = _read_item_title(path)
            assert "(2024)" in title
        finally:
            _cleanup()

    @patch("scripts.enrich_posters.movie_lookup")
    def test_adds_year_and_poster_to_item_without_year_in_title(self, mock_lookup):
        """Items without (YYYY) in title get year + poster."""
        mock_lookup.side_effect = self._mock_lookup
        path = _make_feed([
            {
                "title": "Test Movie",
                "link": "https://example.com/movie/12345",
                "description": "Some description",
            }
        ])
        try:
            changed, stats = process_feed(path)
            assert changed
            assert stats["years"] == 1
            assert stats["posters"] == 1

            title = _read_item_title(path)
            assert "(2024)" in title
            desc = _read_item_desc(path)
            assert "poster1.jpg" in desc
        finally:
            _cleanup()

    @patch("scripts.enrich_posters.movie_lookup")
    @patch("scripts.enrich_posters.search_movie")
    def test_stats_count_correctly(self, mock_search, mock_lookup):
        def _lookup(id):
            if id == 11111:
                return MovieInfo(poster_url="https://img.com/p1.jpg", year="2024", title="Movie A")
            if id == 22222:
                return MovieInfo(poster_url="https://img.com/p2.jpg", year="2025", title="Movie B")
            return MovieInfo()

        mock_lookup.side_effect = _lookup
        mock_search.return_value = MovieInfo()

        path = _make_feed([
            {"title": "Movie A", "link": "https://ex.com/movie/11111", "description": "desc"},
            {"title": "Movie B", "link": "https://ex.com/movie/22222", "description": "desc"},
            {"title": "No Match (2024)", "link": "https://ex.com/movie/33333", "description": "desc"},
        ])
        try:
            changed, stats = process_feed(path)
            assert changed
            assert stats["items"] == 3
            assert stats["posters"] == 2
            assert stats["years"] == 2
        finally:
            _cleanup()

    @patch("scripts.enrich_posters.movie_lookup")
    def test_empty_description(self, mock_lookup):
        mock_lookup.side_effect = self._mock_lookup
        path = _make_feed([
            {
                "title": "Test Movie (2024)",
                "link": "https://example.com/movie/12345",
                "description": "Original desc",
            }
        ])
        try:
            # Clear the item description to simulate missing text
            tree = ET.parse(path)
            for desc in tree.findall(".//item/description"):
                desc.text = ""
            tree.write(path, encoding="UTF-8", xml_declaration=True)

            changed, stats = process_feed(path)
            assert changed
            assert stats["posters"] == 1

            desc = _read_item_desc(path)
            # With empty text, poster replaces the empty string entirely
            assert IMG_TAG_RE.search(desc) if desc else True
        finally:
            _cleanup()
