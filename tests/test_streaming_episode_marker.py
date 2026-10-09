"""TV item titles must keep their episode marker through enrichment.

The canonical-title replacement used to rewrite the whole title to TMDb's
series name, so every episode of a series shipped with the same title
("Reasonable Doubt (2022)") and the reader could not tell them apart. The
source marker (" - 4x3", " S02E03") has to survive the replacement.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch

from core.tmdb import MovieInfo
from scripts.enrichers import streaming_enricher as se


def _write_feed(path, title: str, link: str) -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = link
    ET.SubElement(item, "description").text = "<p>summary</p>"
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def _process(path, info: MovieInfo, **kwargs) -> str:
    """Run process_feed with the TMDb lookups pinned to `info`."""
    with (
        patch.object(se, "tv_lookup", return_value=None),
        patch.object(se, "movie_lookup", return_value=None),
        patch.object(se, "find_by_imdb", return_value=None),
        patch.object(se, "search_tv", return_value=info),
        patch.object(se, "search_movie", return_value=info),
    ):
        se.process_feed(path, epguides_mapping={}, **kwargs)
    title = ET.parse(path).getroot().find("channel/item/title")
    return (title.text if title is not None else "") or ""


class TestEpisodeMarkerSurvives:
    def test_nxm_marker_is_kept(self, tmp_path):
        feed = tmp_path / "tv.xml"
        _write_feed(feed, "Reasonable Doubt - 4x3", "https://example.com/tv/550")
        title = _process(
            feed,
            MovieInfo(poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
                      title="Reasonable Doubt", year="2022", tmdb_id=550,
                      media_type="tv"),
        )
        assert "Reasonable Doubt" in title
        assert "4x3" in title, title

    def test_sxxeyy_marker_is_kept(self, tmp_path):
        feed = tmp_path / "tv.xml"
        _write_feed(feed, "The Gentlemen S02E03", "https://example.com/tv/550")
        title = _process(
            feed,
            MovieInfo(poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
                      title="The Gentlemen", year="2024", tmdb_id=550,
                      media_type="tv"),
        )
        assert "The Gentlemen" in title
        assert "S02E03" in title, title

    def test_marker_not_duplicated(self, tmp_path):
        feed = tmp_path / "tv.xml"
        _write_feed(feed, "The Gentlemen - 1x2", "https://example.com/tv/550")
        info = MovieInfo(poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
                         title="The Gentlemen - 1x2", year="2024", tmdb_id=550,
                         media_type="tv")
        title = _process(feed, info)
        assert title.count("1x2") == 1, title

    def test_movie_titles_are_untouched(self, tmp_path):
        feed = tmp_path / "movie.xml"
        _write_feed(feed, "A Prophet", "https://example.com/movie/9")
        title = _process(
            feed,
            MovieInfo(poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
                      title="A Prophet", year="2026", tmdb_id=9,
                      media_type="movie"),
            is_series_feed=False,
        )
        assert title == "A Prophet (2026)", title
