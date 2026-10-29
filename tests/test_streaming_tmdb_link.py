"""TMDb external links are placed immediately below IMDb in feed descriptions."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch

from core.tmdb import MovieInfo
from scripts.enrichers import streaming_enricher as se


def _feed(path, description: str) -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = "The Show"
    ET.SubElement(item, "link").text = "https://example.test/item"
    ET.SubElement(item, "description").text = description
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _run(path, info: MovieInfo) -> None:
    with (
        patch.object(se, "_lookup_link", return_value=info),
        patch.object(se, "_epguides_map", return_value={}),
    ):
        se.process_feed(path, epguides_mapping={}, is_series_feed=False)


def test_tmdb_link_is_below_imdb_and_idempotent(tmp_path):
    feed = tmp_path / "movie.xml"
    _feed(feed, '<img src="https://image.tmdb.org/t/p/w500/poster.jpg">')
    info = MovieInfo(
        poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
        title="The Show",
        year="2025",
        tmdb_id=12345,
        media_type="movie",
        imdb_id="tt1010101",
    )

    _run(feed, info)
    text = feed.read_text(encoding="utf-8")
    description = ET.parse(feed).getroot().find("channel/item/description").text
    assert "https://www.themoviedb.org/movie/12345" in text
    assert description.index("IMDb</b>") < description.index("TMDb</b>")

    before = feed.read_text(encoding="utf-8")
    _run(feed, info)
    assert feed.read_text(encoding="utf-8") == before


def test_missing_tmdb_id_uses_search_link_below_existing_imdb(tmp_path):
    feed = tmp_path / "movie.xml"
    imdb = (
        '<a href="https://www.imdb.com/title/tt1010101/">'
        '<b style="color:#6600cc;">IMDb</b></a>'
    )
    _feed(feed, f'<img src="https://image.tmdb.org/p/t.jpg">{imdb}')
    info = MovieInfo(
        poster_url="https://image.tmdb.org/p/t.jpg",
        title="The Show",
        year="2025",
        tmdb_id=None,
        media_type="movie",
        imdb_id="tt1010101",
    )

    _run(feed, info)
    description = ET.parse(feed).getroot().find("channel/item/description").text
    assert "https://www.themoviedb.org/search?query=The%20Show" in description
    assert description.index("IMDb</b>") < description.index("TMDb</b>")


def test_tmdb_above_imdb_is_moved_underneath_and_converges(tmp_path):
    """Items the older block enriched carry TMDb above IMDb. The link guard
    would leave that order forever, so the write path moves the anchor below
    IMDb once and then leaves the bytes alone."""
    feed = tmp_path / "movie.xml"
    tmdb = (
        '<a href="https://www.themoviedb.org/movie/12345" target="_blank" '
        'rel="noopener noreferrer"><b style="color:#01b4e4;">TMDb</b></a>'
    )
    imdb = (
        '<a href="https://www.imdb.com/title/tt1010101/">'
        '<b style="color:#6600cc;">IMDb</b></a>'
    )
    _feed(feed, f'<img src="https://image.tmdb.org/p/t.jpg">{tmdb}<br>{imdb}')
    info = MovieInfo(
        poster_url="https://image.tmdb.org/p/t.jpg",
        title="The Show",
        year="2025",
        tmdb_id=12345,
        media_type="movie",
        imdb_id="tt1010101",
    )

    _run(feed, info)
    description = ET.parse(feed).getroot().find("channel/item/description").text
    assert description.index("IMDb</b>") < description.index("TMDb</b>")
    assert description.count("TMDb</b>") == 1

    before = feed.read_text(encoding="utf-8")
    _run(feed, info)
    assert feed.read_text(encoding="utf-8") == before


class TestMoveTmdbUnderImdb:
    def test_no_op_when_tmdb_is_already_below(self):
        text = (
            '<a href="https://www.imdb.com/title/tt1/">IMDb</a>'
            '<br><a href="https://www.themoviedb.org/movie/1">TMDb</a>'
        )
        assert se._move_tmdb_under_imdb(text) == text

    def test_no_op_without_an_imdb_anchor(self):
        text = '<a href="https://www.themoviedb.org/movie/1">TMDb</a>'
        assert se._move_tmdb_under_imdb(text) == text

    def test_moves_the_anchor_and_leaves_one_separator(self):
        text = (
            'img<br><a href="https://www.themoviedb.org/movie/1">TMDb</a>'
            '<br><a href="https://www.imdb.com/title/tt1/">IMDb</a><br>T'
        )
        moved = se._move_tmdb_under_imdb(text)
        assert moved.index("IMDb") < moved.index("TMDb")
        assert "<br><br>" not in moved
        assert moved.count("TMDb") == 1
        assert se._move_tmdb_under_imdb(moved) == moved

    def test_duplicate_anchors_above_imdb_collapse_to_one(self):
        """Only the first survives, and the repair converges.

        Leaving a second anchor above IMDb would re-trigger the move on every
        run, so the item would never settle and the hourly deploy would rewrite
        it forever.
        """
        text = (
            '<a href="https://www.themoviedb.org/movie/1">TMDb</a>'
            '<br><a href="https://www.themoviedb.org/search?query=x">TMDb</a>'
            '<br><a href="https://www.imdb.com/title/tt1/">IMDb</a>'
        )
        moved = se._move_tmdb_under_imdb(text)
        assert moved.count("TMDb</a>") == 1
        assert "themoviedb.org/movie/1" in moved
        assert moved.index("IMDb") < moved.index("TMDb")
        assert "<br><br>" not in moved
        assert se._move_tmdb_under_imdb(moved) == moved


def test_tmdb_link_builder_rejects_invalid_ids_and_media_types():
    assert se._build_tmdb_link("<script>", "movie", "The Show") == (
        '<a href="https://www.themoviedb.org/search?query=The%20Show" target="_blank" '
        'rel="noopener noreferrer"><b style="color:#01b4e4;">TMDb</b></a>'
    )
    assert se._build_tmdb_link(123, "person", "The Show") == (
        '<a href="https://www.themoviedb.org/search?query=The%20Show" target="_blank" '
        'rel="noopener noreferrer"><b style="color:#01b4e4;">TMDb</b></a>'
    )
