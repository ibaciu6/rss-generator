"""Player embed links added to torrent/release feeds.

VidSrc accepts a TMDb *or* IMDb id and takes the season/episode as path
segments (``/embed/tv/{id}/{season}/{episode}``), and unlike CineSrc it also
reads the "4x3" episode marker. These tests pin the link shapes and the
idempotency guards that keep hourly CI from duplicating the links.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch

from core.tmdb import MovieInfo
from scripts.enrichers import streaming_enricher as se
from scripts.enrichers.player_servers import build_player_link


def _write_feed(path, title: str, link: str, desc: str = "<p>summary</p>") -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = link
    ET.SubElement(item, "description").text = desc
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def _process(path, info: MovieInfo, **kwargs):
    with (
        patch.object(se, "movie_lookup", return_value=info),
        patch.object(se, "tv_lookup", return_value=info if info.media_type == "tv" else None),
        patch.object(se, "find_by_imdb", return_value=None),
        patch.object(se, "search_movie", return_value=info),
        patch.object(se, "search_tv", return_value=info),
    ):
        return se.process_feed(path, epguides_mapping={}, **kwargs)


class TestBuildVidsrcLink:
    def test_no_id_is_none(self):
        assert build_player_link("vidsrc", None, None, "movie") is None
        assert build_player_link("vidsrc", None, "", "tv") is None

    def test_movie_uses_tmdb_id(self):
        link = build_player_link("vidsrc", 927085, None, "movie")
        assert "https://vidsrc.to/embed/movie/927085" in link

    def test_imdb_id_is_preferred(self):
        link = build_player_link("vidsrc", 12345, "tt17048514", "movie")
        assert "https://vidsrc.to/embed/movie/tt17048514" in link
        assert "12345" not in link

    def test_tv_episode_from_sxxeyy(self):
        link = build_player_link("vidsrc", None, "tt18382028", "tv", "The Show S02E03")
        assert "https://vidsrc.to/embed/tv/tt18382028/2/3" in link

    def test_tv_episode_from_nxm(self):
        link = build_player_link("vidsrc", 158876, None, "tv", "The Show - 4x3")
        assert "https://vidsrc.to/embed/tv/158876/4/3" in link

    def test_tv_series_without_marker_gets_series_link(self):
        link = build_player_link("vidsrc", 158876, None, "tv", "The Show")
        assert 'href="https://vidsrc.to/embed/tv/158876"' in link

    def test_unknown_server_is_none(self):
        assert build_player_link("nope", 12345, None, "movie") is None


class TestVidsrcOnTorrentFeeds:
    def test_movie_item_gets_both_player_links(self, tmp_path):
        feed = tmp_path / "uindex-movies.xml"
        desc = (
            '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
            '<br><a href="https://www.imdb.com/find?q=X&s=tt"><b>IMDb</b></a>'
        )
        _write_feed(feed, "Some.Movie.2026.1080p.WEB-DL", "https://tmdb/movie/12345", desc=desc)
        info = MovieInfo(
            poster_url="https://image.tmdb.org/t/p/w342/x.jpg", title="Some Movie",
            year="2026", tmdb_id=12345, media_type="movie", imdb_id="tt1234567",
        )
        _process(feed, info, feed_category="torrents")
        text = feed.read_text(encoding="utf-8")
        assert "CineSrc" in text and "VidSrc" in text, text
        # CineSrc takes the TMDB id, VidSrc prefers the IMDb id -- asserting both
        # catches a server that silently fell back to the wrong id.
        assert "https://cinesrc.st/embed/movie/12345" in text, text
        assert "https://vidsrc.to/embed/movie/tt1234567" in text, text
        assert text.count("VidSrc") == 1, text

    def test_tv_item_gets_episode_link(self, tmp_path):
        feed = tmp_path / "uindex-tv.xml"
        _write_feed(feed, "The Show S02E03 1080p WEB-DL", "https://tmdb/tv/550")
        info = MovieInfo(
            poster_url="https://image.tmdb.org/t/p/w342/x.jpg", title="The Show",
            year="2024", tmdb_id=550, media_type="tv", imdb_id="tt1000000",
        )
        _process(feed, info, feed_category="torrents", is_series_feed=True)
        text = feed.read_text(encoding="utf-8")
        assert "https://vidsrc.to/embed/tv/tt1000000/2/3" in text, text

    def test_non_torrent_feed_gets_no_vidsrc(self, tmp_path):
        feed = tmp_path / "atlantic-movies.xml"
        _write_feed(feed, "Some Movie", "https://tmdb/movie/12345")
        info = MovieInfo(
            poster_url="https://image.tmdb.org/t/p/w342/x.jpg", title="Some Movie",
            year="2026", tmdb_id=12345, media_type="movie",
        )
        _process(feed, info, feed_category="streaming", is_series_feed=False)
        assert "VidSrc" not in feed.read_text(encoding="utf-8")

    def test_rerun_does_not_duplicate(self, tmp_path):
        feed = tmp_path / "uindex-movies.xml"
        _write_feed(feed, "Some.Movie.2026.1080p.WEB-DL", "https://tmdb/movie/12345")
        info = MovieInfo(
            poster_url="https://image.tmdb.org/t/p/w342/x.jpg", title="Some Movie",
            year="2026", tmdb_id=12345, media_type="movie", imdb_id="tt1234567",
        )
        _process(feed, info, feed_category="torrents", keep_titles=True)
        changed, _ = _process(feed, info, feed_category="torrents", keep_titles=True)
        text = feed.read_text(encoding="utf-8")
        assert text.count("VidSrc") == 1, text
        assert text.count("CineSrc") == 1, text
        assert changed is False, text

    def test_item_missing_vidsrc_gets_it_added(self, tmp_path):
        """An item enriched when only CineSrc existed is still "already
        enriched"; the one-time VidSrc backfill must still fire."""
        feed = tmp_path / "uindex-movies.xml"
        desc = (
            '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
            '<br><a href="https://www.imdb.com/title/tt1234567/" target="_blank">'
            '<b style="color:#6600cc;">IMDb</b></a>'
            '<br><a href="https://cinesrc.st/embed/movie/12345" target="_blank">'
            '<b style="color:#6600cc;">CineSrc</b></a>'
        )
        _write_feed(feed, "Some.Movie.2026.1080p.WEB-DL", "https://tmdb/movie/12345", desc=desc)
        info = MovieInfo(
            poster_url="https://image.tmdb.org/t/p/w342/x.jpg", title="Some Movie",
            year="2026", tmdb_id=12345, media_type="movie", imdb_id="tt1234567",
        )
        _process(feed, info, feed_category="torrents", keep_titles=True)
        text = feed.read_text(encoding="utf-8")
        assert text.count("CineSrc") == 1, text
        assert text.count("VidSrc") == 1, text

    def test_player_servers_override(self, tmp_path):
        """Explicit player_servers replaces the per-category default."""
        feed = tmp_path / "uindex-movies.xml"
        _write_feed(feed, "Some.Movie.2026.1080p.WEB-DL", "https://tmdb/movie/12345")
        info = MovieInfo(
            poster_url="https://image.tmdb.org/t/p/w342/x.jpg", title="Some Movie",
            year="2026", tmdb_id=12345, media_type="movie", imdb_id="tt1234567",
        )
        _process(
            feed,
            info,
            feed_category="torrents",
            keep_titles=True,
            player_servers=("vidsrc",),
        )
        text = feed.read_text(encoding="utf-8")
        assert "VidSrc" in text, text
        assert "CineSrc" not in text, text

    def test_empty_player_servers_disables(self, tmp_path):
        """player_servers: [] disables player links for a torrent feed."""
        feed = tmp_path / "uindex-movies.xml"
        _write_feed(feed, "Some.Movie.2026.1080p.WEB-DL", "https://tmdb/movie/12345")
        info = MovieInfo(
            poster_url="https://image.tmdb.org/t/p/w342/x.jpg", title="Some Movie",
            year="2026", tmdb_id=12345, media_type="movie", imdb_id="tt1234567",
        )
        _process(feed, info, feed_category="torrents", keep_titles=True, player_servers=())
        text = feed.read_text(encoding="utf-8")
        assert "VidSrc" not in text, text
        assert "CineSrc" not in text, text