"""TV item titles must keep their episode marker through enrichment.

The canonical-title replacement used to rewrite the whole title to TMDb's
series name, so every episode of a series shipped with the same title
("Reasonable Doubt (2022)") and the reader could not tell them apart. The
source marker (" - 4x3", " S02E03") has to survive the replacement.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest

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


class TestTorrentCinesrc:
    """A seeded torrent item enriched before CineSrc existed is "already
    enriched" (TMDb poster + IMDb link + year) and used to skip forever, so
    uindex shipped without its CineSrc embed. It must be added now."""

    def test_already_enriched_torrent_still_gets_cinesrc(self, tmp_path):
        feed = tmp_path / "uindex-movies.xml"
        desc = (
            '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
            '<br><a href="https://www.imdb.com/find?q=X&s=tt"><b>IMDb</b></a>'
            '<br><strong>Size:</strong> 1.6 GB'
        )
        _write_feed(feed, "Some.Movie.2026.1080p.WEB-DL", "https://tmdb/movie/12345",
                    desc=desc)
        info = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
                         title="Some Movie", year="2026", tmdb_id=12345,
                         media_type="movie")
        with (
            patch.object(se, "movie_lookup", return_value=info),
            patch.object(se, "tv_lookup", return_value=None),
            patch.object(se, "find_by_imdb", return_value=None),
            patch.object(se, "search_movie", return_value=info),
            patch.object(se, "search_tv", return_value=None),
        ):
            se.process_feed(feed, epguides_mapping={}, feed_category="torrents")
        text = feed.read_text(encoding="utf-8")
        assert "CineSrc" in text, text
        assert text.count("CineSrc") == 1, text


class TestImdbDirectLinkUpgrade:
    """An item enriched when only the /find search link existed must be
    upgraded in place to a direct /title/tt…/ link -- not given a second IMDb
    link -- and must then be left alone on subsequent runs."""

    _DESC = (
        '<img src="https://image.tmdb.org/t/p/w342/x.jpg">'
        '<br><a href="https://www.imdb.com/find?q=Matchbox%20(2026)&amp;s=tt" '
        'target="_blank" rel="noopener noreferrer">'
        '<b style="color:#6600cc;">IMDb</b></a>'
    )

    def _run(self, path, info, **kwargs) -> tuple[bool, str]:
        with (
            patch.object(se, "tv_lookup", return_value=None),
            patch.object(se, "movie_lookup", return_value=info),
            patch.object(se, "find_by_imdb", return_value=None),
            patch.object(se, "search_tv", return_value=None),
            patch.object(se, "search_movie", return_value=None),
        ):
            changed, _stats = se.process_feed(path, epguides_mapping={}, **kwargs)
        return changed, path.read_text(encoding="utf-8")

    def test_find_link_upgraded_to_title_link(self, tmp_path):
        feed = tmp_path / "scenereleases.xml"
        _write_feed(feed, "Matchbox.The.Movie.2026.1080p.WEB-DL",
                    "https://tmdb/movie/12345", desc=self._DESC)
        info = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
                         title="Matchbox The Movie", year="2026", tmdb_id=12345,
                         media_type="movie", imdb_id="tt28498219")
        changed, text = self._run(feed, info)
        assert changed is True
        assert "https://www.imdb.com/title/tt28498219/" in text, text
        assert "imdb.com/find" not in text, text
        # Upgraded in place: exactly one IMDb link, label intact.
        assert text.count("IMDb") == 1, text

    def test_upgraded_item_is_left_alone_on_the_next_run(self, tmp_path):
        feed = tmp_path / "scenereleases.xml"
        _write_feed(feed, "Matchbox.The.Movie.2026.1080p.WEB-DL",
                    "https://tmdb/movie/12345", desc=self._DESC)
        info = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
                         title="Matchbox The Movie", year="2026", tmdb_id=12345,
                         media_type="movie", imdb_id="tt28498219")
        # keep_titles keeps the 2026 in the title, so the already-enriched
        # guard (which only skips year-bearing items) fires on the second run.
        self._run(feed, info, keep_titles=True)
        changed, text = self._run(feed, info, keep_titles=True)
        assert changed is False, text
        assert "https://www.imdb.com/title/tt28498219/" in text, text


class TestUnknownPlayerServerIsRejected:
    """An unvalidated server id must raise, not silently drop links.

    Configured ids are validated at load time (core/config.py), so this only
    fires when something calls process_feed directly with a bad id.
    """
    def _run(self, path, info, **kwargs) -> tuple[bool, str]:
        with (
            patch.object(se, "tv_lookup", return_value=None),
            patch.object(se, "movie_lookup", return_value=info),
            patch.object(se, "find_by_imdb", return_value=None),
            patch.object(se, "search_tv", return_value=None),
            patch.object(se, "search_movie", return_value=None),
        ):
            changed, _stats = se.process_feed(path, epguides_mapping={}, **kwargs)
        return changed, path.read_text(encoding="utf-8")

    def test_unknown_server_id_raises_value_error(self, tmp_path):
        feed = tmp_path / "scenereleases.xml"
        _write_feed(feed, "Matchbox.The.Movie.2026.1080p.WEB-DL",
                    "https://tmdb/movie/12345", "<p>summary</p>")
        # process_feed validates the ids before touching the feed, so no
        # TMDB lookups need to be pinned for this one.
        with pytest.raises(ValueError) as excinfo:
            se.process_feed(feed, epguides_mapping={},
                            player_servers=("nosuchserver",))
        assert "nosuchserver" in str(excinfo.value)

    def test_empty_tuple_disables_player_links(self, tmp_path):
        feed = tmp_path / "scenereleases.xml"
        _write_feed(feed, "Matchbox.The.Movie.2026.1080p.WEB-DL",
                    "https://tmdb/movie/12345", "<p>summary</p>")
        info = MovieInfo(poster_url="https://image.tmdb.org/t/p/w342/x.jpg",
                         title="Matchbox The Movie", year="2026", tmdb_id=12345,
                         media_type="movie", imdb_id="tt28498219")
        _, text = self._run(feed, info, player_servers=())
        # Other enrichments (poster, Trailer, IMDb) still run; only the
        # player links are disabled by the empty tuple.
        assert "CineSrc" not in text, text
        assert "VidSrc" not in text, text
