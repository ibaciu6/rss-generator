"""How a feed item turns into a direct IMDb title link.

Three failures are pinned here:

* A site that encodes its own ids in ``/movie/5…`` URLs (hydrahd) made
  ``_lookup_link`` return an *empty* MovieInfo, not ``None``. The caller reads
  ``None`` as "search by title", so an apparently alive-but-empty result
  dead-ended the item: no search, no links, no upgrade, and no skip -- the
  item silently kept its old ``/find`` link on every run.
* A feed with no sites.yaml entry states no ``kind``. When the item title also
  carries no SxxEyy marker (bingebang lists episodes by name), the item was
  searched as a *movie* and its series never resolved. The item URL is the
  remaining signal.
* TMDb's own record sometimes has no IMDb id (old entries ship an empty
  string, brand-new ones none at all). IMDb's index knows the title; the
  fallback can fill the id, but only when the film it names still matches the
  item -- a wrong id is worse than keeping the honest ``/find`` search link.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch

from core.tmdb import MovieInfo
from scripts.enrichers import streaming_enricher as se


def _write_feed(path, title: str, link: str, desc: str = "<p>summary</p>") -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = link
    ET.SubElement(item, "description").text = desc
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


class TestLookupLinkEmptyIsAMiss:
    """An empty lookup must read as ``None``, not as a dead answer."""

    def test_empty_lookup_is_none(self):
        with patch.object(se, "_lookup", return_value=MovieInfo()):
            result = se._lookup_link(
                "https://hydrahd.com/movie/205710-watch-unit-23-reloaded-2024-online"
            )
        assert result is None

    def test_empty_imdb_lookup_is_none(self):
        with patch.object(se, "find_by_imdb", return_value=MovieInfo()):
            assert se._lookup_link("https://www.imdb.com/title/tt1234567/") is None

    def test_poster_bearing_lookup_is_kept(self):
        info = MovieInfo(poster_url="https://image.tmdb.org/t/p/w500/x.jpg")
        with patch.object(se, "_lookup", return_value=info):
            assert se._lookup_link("https://tmdb/movie/12345") is info


class TestSeriesUrlSignal:
    """An unconfigured feed's ``/tv/`` URL marks the item as a series."""

    def test_regex_matches_tv_and_series_paths(self):
        assert se.TV_DETAIL_URL_RE.search("https://bingebang.st/tv/watch/dark-matter-f6mre3")
        assert se.TV_DETAIL_URL_RE.search("https://bingebang.st/series/watch/x")
        assert not se.TV_DETAIL_URL_RE.search("https://bingebang.st/movie/watch/the-fix-2026")

    def test_series_url_item_resolves_through_search_tv(self, tmp_path):
        feed = tmp_path / "bingebang-tv.xml"
        _write_feed(feed, "Dark Matter", "https://bingebang.st/tv/watch/dark-matter-f6mre3")
        tv = MovieInfo(poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
                       title="Dark Matter", year="2024", tmdb_id=196322,
                       media_type="tv", imdb_id="tt19231492")
        with (
            patch.object(se, "tv_lookup", return_value=None),
            patch.object(se, "movie_lookup", return_value=None),
            patch.object(se, "find_by_imdb", return_value=None),
            patch.object(se, "search_tv", return_value=tv) as search_tv,
            patch.object(se, "search_movie", return_value=None) as search_movie,
        ):
            se.process_feed(feed, epguides_mapping={}, is_series_feed=None)
        assert search_tv.called and not search_movie.called
        assert "https://www.imdb.com/title/tt19231492/" in feed.read_text(encoding="utf-8")

    def test_movie_url_item_still_searches_movies(self, tmp_path):
        feed = tmp_path / "bingebang-movies.xml"
        _write_feed(feed, "The Fix", "https://bingebang.st/movie/watch/the-fix-2026-076zyn")
        movie = MovieInfo(poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
                          title="The Fix", year="2026", tmdb_id=999,
                          media_type="movie", imdb_id="tt1111111")
        with (
            patch.object(se, "tv_lookup", return_value=None),
            patch.object(se, "movie_lookup", return_value=None),
            patch.object(se, "find_by_imdb", return_value=None),
            patch.object(se, "search_tv", return_value=None) as search_tv,
            patch.object(se, "search_movie", return_value=movie) as search_movie,
        ):
            se.process_feed(feed, epguides_mapping={}, is_series_feed=None)
        assert search_movie.called and not search_tv.called


class TestFallbackFillsMissingImdbId:
    """TMDb has no imdb_id -> ask IMDb's index, verify, then upgrade."""

    _DESC = (
        '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
        '<br><a href="https://www.imdb.com/find?q=The%20Great%20British%20Bake%20Off'
        '&amp;s=tt&amp;ttype=tv"><b>IMDb</b></a>'
    )

    def _run(self, feed, *, details, fallback):
        with (
            patch.object(se, "movie_lookup", return_value=None),
            patch.object(se, "tv_lookup", return_value=details),
            patch.object(se, "find_by_imdb", return_value=None),
            patch.object(se, "search_movie", return_value=None),
            patch.object(se, "search_tv", return_value=None),
            patch.object(se, "_fallback_lookup", return_value=fallback),
        ):
            se.process_feed(feed, epguides_mapping={}, is_series_feed=True)

    def test_matching_fallback_upgrades_to_title_link(self, tmp_path):
        feed = tmp_path / "uindex-tv.xml"
        _write_feed(feed, "The Great British Bake Off S17E03 Audience Choice Week",
                    "https://www.themoviedb.org/tv/87012", desc=self._DESC)
        # TMDb's own external_ids holds an empty imdb_id for this old entry.
        details = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
                            title="The Great British Bake Off", year="2017",
                            tmdb_id=87012, media_type="tv", imdb_id=None)
        fallback = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
                             title="The Great British Bake Off", year="2017",
                             tmdb_id=87012, media_type="tv", imdb_id="tt1877368")
        self._run(feed, details=details, fallback=fallback)
        text = feed.read_text(encoding="utf-8")
        assert "https://www.imdb.com/title/tt1877368/" in text
        assert "imdb.com/find" not in text

    def test_mismatched_fallback_keeps_the_search_link(self, tmp_path):
        # atlantic-tv's "Monster" season: the fallback names "Monster High",
        # a different show. Pointing IMDb at it would be a lie.
        feed = tmp_path / "atlantic-tv.xml"
        _write_feed(feed, "Monster: The Lizzie Borden Story (2026)",
                    "https://www.themoviedb.org/tv/299939", desc=self._DESC)
        details = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
                            title="Monster: The Lizzie Borden Story", year="2026",
                            tmdb_id=299939, media_type="tv", imdb_id=None)
        fallback = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/y.jpg",
                             title="Monster High", year="2022", tmdb_id=131532,
                             media_type="tv", imdb_id="tt13207736")
        self._run(feed, details=details, fallback=fallback)
        text = feed.read_text(encoding="utf-8")
        assert "tt13207736" not in text
        assert "imdb.com/find" in text
