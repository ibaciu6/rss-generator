"""IMDb/OMDb fallback searches used when TMDb's own search misses.

Both helpers must filter the returned candidates by IMDb's type tag (`qid`):
a series is spelled "tvSeries"/"tvMiniSeries", not "tv", so a filter that
expected "tv" silently skipped every one of them.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from core.tmdb import MovieInfo
from scripts.enrichers import streaming_enricher as se


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _suggest_payload():
    """A suggestion response with a movie and a series of the same name."""
    return {
        "v": 3,
        "d": [
            {"id": "tt1010101", "l": "The Show", "y": 2025, "qid": "movie"},
            {"id": "tt2020202", "l": "The Show", "y": 2022, "qid": "tvSeries"},
            {"id": "tt3030303", "l": "The Show (podcast)", "y": 2021, "qid": "TVEpisode"},
        ],
    }


def test_imdb_suggest_prefers_series_for_tv(monkeypatch):
    """A TV item must resolve to the tvSeries entry, not the movie."""
    monkeypatch.setattr(
        se.httpx, "get", lambda *a, **k: _Resp(_suggest_payload())
    )
    hit = se._imdb_suggest("The Show", prefer="tv")
    assert hit is not None
    assert hit[0] == "tt2020202"


def test_imdb_suggest_prefers_movie_for_movie(monkeypatch):
    monkeypatch.setattr(
        se.httpx, "get", lambda *a, **k: _Resp(_suggest_payload())
    )
    hit = se._imdb_suggest("The Show", prefer="movie")
    assert hit is not None
    assert hit[0] == "tt1010101"


def test_imdb_suggest_year_narrows_first_pool(monkeypatch):
    """A matching year on the right type outranks the same-type hit."""
    monkeypatch.setattr(
        se.httpx, "get", lambda *a, **k: _Resp(_suggest_payload())
    )
    hit = se._imdb_suggest("The Show", year="2022", prefer="movie")
    assert hit[0] == "tt1010101"  # 2025 movie; 2022 only matches the series


def test_imdb_suggest_skips_episode_qid(monkeypatch):
    """Non-screen qids (TVEpisode, etc.) must not be returned."""
    monkeypatch.setattr(
        se.httpx, "get", lambda *a, **k: _Resp(_suggest_payload())
    )
    hit = se._imdb_suggest("The Show", prefer="tv")
    assert hit[0] != "tt3030303"


def test_imdb_suggest_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(se.httpx, "get", lambda *a, **k: _Resp({"d": []}))
    assert se._imdb_suggest("No Such Thing") is None


def _omdb_fake_get():
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        if params.get("t"):
            # Exact-title lookup misses this fresh release.
            return _Resp({"Response": "False", "Error": "Movie not found!"})
        # ?s= search: movie first, series second.
        return _Resp(
            {
                "Search": [
                    {"Title": "The Show", "Year": "2025", "imdbID": "tt1010101", "Type": "movie"},
                    {"Title": "The Show", "Year": "2022", "imdbID": "tt2020202", "Type": "series"},
                ]
            }
        )

    return fake_get, calls


def test_omdb_search_prefers_movie_type(monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "omdb-key")
    fake_get, calls = _omdb_fake_get()
    monkeypatch.setattr(se.httpx, "get", fake_get)
    hit = se._omdb_search("The Show", prefer="movie")
    assert hit is not None and hit[0] == "tt1010101"
    # ?t= (with and without y applies here) then the ?s= fallback.
    assert any("s" in c for c in calls)


def test_omdb_search_exact_title_with_year_is_tried_first(monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "omdb-key")
    fake_get, calls = _omdb_fake_get()
    monkeypatch.setattr(se.httpx, "get", fake_get)
    se._omdb_search("The Show", year="2025", prefer="movie")
    first = calls[0]
    assert first["t"] == "The Show"
    assert first["y"] == "2025"
    assert first["apikey"] == "omdb-key"


def test_omdb_search_requires_key(monkeypatch):
    monkeypatch.delenv("OMDB_API_KEY", raising=False)
    fake_get, _calls = _omdb_fake_get()
    monkeypatch.setattr(se.httpx, "get", fake_get)
    assert se._omdb_search("The Show") is None


@pytest.mark.parametrize(
    "candidates,prefer,year,expected_id",
    [
        ([], "movie", None, None),
        (
            [{"id": "tt1", "title": "A", "year": "2024", "qid": "movie"}],
            "movie",
            None,
            "tt1",
        ),
        (
            [
                {"id": "tt2", "title": "B", "year": "2020", "qid": "tvSeries"},
                {"id": "tt1", "title": "A", "year": "2024", "qid": "movie"},
            ],
            "movie",
            "2024",
            "tt1",
        ),
        # Year only applies within the preferred type: no 2024 movie exists, so
        # the type-only pool returns the 2022 movie rather than the 2024 series.
        (
            [
                {"id": "tt1", "title": "A", "year": "2024", "qid": "tvSeries"},
                {"id": "tt2", "title": "B", "year": "2022", "qid": "movie"},
            ],
            "movie",
            "2024",
            "tt2",
        ),
    ],
)
def test_pick_imdb_hit(candidates, prefer, year, expected_id):
    hit = se._pick_imdb_hit(candidates, year, prefer)
    assert (hit[0] if hit else None) == expected_id


def _omdb_by_id_payload(**kw):
    base = {
        "Title": "The Show",
        "Year": "2025",
        "imdbID": "tt1234567",
        "Type": "movie",
        "Poster": "https://m.media-amazon.com/images/M/x.jpg",
        "Response": "True",
    }
    base.update(kw)
    return base


def test_omdb_by_id_returns_canonical_record(monkeypatch):
    """The ?i= form returns the canonical (title, year, kind, poster)."""
    monkeypatch.setenv("OMDB_API_KEY", "omdb-key")
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _Resp(_omdb_by_id_payload())

    monkeypatch.setattr(se.httpx, "get", fake_get)
    assert se._omdb_by_id("tt1234567") == (
        "The Show",
        "2025",
        "movie",
        "https://m.media-amazon.com/images/M/x.jpg",
    )
    assert calls[0]["i"] == "tt1234567"
    assert calls[0]["apikey"] == "omdb-key"


def test_omdb_by_id_series_kind(monkeypatch):
    """A series record maps to TMDb's "tv" spelling, not OMDb's "series"."""
    monkeypatch.setenv("OMDB_API_KEY", "omdb-key")
    monkeypatch.setattr(
        se.httpx, "get", lambda *a, **k: _Resp(_omdb_by_id_payload(Type="series"))
    )
    assert se._omdb_by_id("tt1234567")[2] == "tv"


def test_omdb_by_id_poster_na_is_none(monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "omdb-key")
    monkeypatch.setattr(
        se.httpx, "get", lambda *a, **k: _Resp(_omdb_by_id_payload(Poster="N/A"))
    )
    assert se._omdb_by_id("tt1234567")[3] is None


def test_omdb_by_id_requires_key(monkeypatch):
    monkeypatch.delenv("OMDB_API_KEY", raising=False)
    monkeypatch.setattr(
        se.httpx, "get", lambda *a, **k: _Resp(_omdb_by_id_payload())
    )
    assert se._omdb_by_id("tt1234567") is None


def test_omdb_by_id_miss_returns_none(monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "omdb-key")
    monkeypatch.setattr(
        se.httpx,
        "get",
        lambda *a, **k: _Resp({"Response": "False", "Error": "Movie not found!"}),
    )
    assert se._omdb_by_id("tt9999999") is None


def test_fallback_lookup_enriches_from_omdb_poster(monkeypatch):
    """/find and the corrected search miss; the ?i= poster is the last resort."""
    monkeypatch.setattr(
        se, "_imdb_suggest", lambda *a, **k: ("tt1234567", "The Show", "2025")
    )
    monkeypatch.setattr(se, "_omdb_search", lambda *a, **k: None)
    monkeypatch.setattr(se, "find_by_imdb", lambda imdb_id: MovieInfo())
    monkeypatch.setattr(
        se, "search_movie", lambda title, year=None: MovieInfo()
    )
    monkeypatch.setattr(se, "_google_tmdb_id", lambda *a, **k: None)
    monkeypatch.setattr(
        se,
        "_omdb_by_id",
        lambda imdb_id: ("The Show (2025)", "2025", "movie", "https://m.media-amazon.com/z.jpg"),
    )
    info = se._fallback_lookup("The Show", "2025", "movie")
    assert info is not None
    assert info.poster_url == "https://m.media-amazon.com/z.jpg"
    assert info.imdb_id == "tt1234567"
    assert info.media_type == "movie"


def test_fallback_lookup_rediscovers_via_omdb_canonical_title(monkeypatch):
    """A canonical ?i= title that TMDb does index rescues the item first."""
    monkeypatch.setattr(
        se, "_imdb_suggest", lambda *a, **k: ("tt1234567", "The Show", "2025")
    )
    monkeypatch.setattr(se, "_omdb_search", lambda *a, **k: None)
    monkeypatch.setattr(se, "find_by_imdb", lambda imdb_id: MovieInfo())
    tmdb_hit = MovieInfo(
        poster_url="https://image.tmdb.org/t/p/w342/p.jpg",
        title="The Show (2025)",
        year="2025",
        tmdb_id=42,
        media_type="movie",
    )

    def fake_search(title, year=None):
        if title == "The Show (2025)":
            return tmdb_hit
        return MovieInfo()

    monkeypatch.setattr(se, "search_movie", fake_search)
    monkeypatch.setattr(se, "_google_tmdb_id", lambda *a, **k: None)
    monkeypatch.setattr(
        se,
        "_omdb_by_id",
        lambda imdb_id: ("The Show (2025)", "2025", "movie", None),
    )
    info = se._fallback_lookup("The Show", "2025", "movie")
    assert info is not None
    assert info.tmdb_id == 42
    assert info.imdb_id == "tt1234567"


class TestOmdbPosterRescue:
    """OMDb supplies the poster when a resolved IMDb id has no TMDb poster.

    The id only reaches the details step past the title guard, so the poster
    it earns belongs to the film the item is about. The OMDb *record* is
    still re-checked with _title_matches: a renamed film ("The Fix" ->
    "Hotel Tehran") must not inherit the other title's poster, and an item
    that already has a TMDb poster never spends an OMDb call.
    """

    FEED = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>t</title>'
        "<item><title>{title}</title>"
        "<link>https://redd.it/abc</link>"
        "</item></channel></rss>"
    )

    def _enrich(self, tmp_path, monkeypatch, title, lookup, details, omdb):
        path = tmp_path / "rescue-feed.xml"
        path.write_text(self.FEED.format(title=title), encoding="utf-8")
        monkeypatch.setattr(se, "_lookup_link", lambda link: lookup)
        monkeypatch.setattr(se, "movie_lookup", lambda tmdb_id: details)
        monkeypatch.setattr(se, "_omdb_by_id", lambda imdb_id: omdb)
        monkeypatch.setattr(se, "_epguides_map", lambda: {})
        se.process_feed(path, epguides_mapping={}, epguides_misses={},
                        is_series_feed=False, feed_category=None,
                        keep_titles=True)
        return ET.parse(path).getroot().find("channel").findall("item")[0]

    def test_posterless_tmdb_record_gains_the_omdb_poster(self, tmp_path, monkeypatch):
        """No TMDb poster, but the id resolves: OMDb's ?i= poster fills in."""
        lookup = MovieInfo(tmdb_id=999, title="Upgrade", year="2034", media_type="movie")
        details = MovieInfo(imdb_id="tt1234567")
        item = self._enrich(
            tmp_path, monkeypatch, "Upgrade.2034.2160p.WEB-DL", lookup, details,
            ("Upgrade", "2034", "movie", "https://m.media-amazon.com/z.jpg"),
        )
        desc = item.findtext("description") or ""
        assert "https://m.media-amazon.com/z.jpg" in desc
        assert "image.tmdb.org" not in desc
        assert "tt1234567" in desc  # direct IMDb link from the same id

    def test_renamed_film_does_not_take_the_other_titles_poster(self, tmp_path, monkeypatch):
        """tt9601292 is "Hotel Tehran" per OMDb; "The Fix" must not use it."""
        lookup = MovieInfo(tmdb_id=1228834, title="The Fix", year="2026", media_type="movie")
        details = MovieInfo(imdb_id="tt9601292")
        item = self._enrich(
            tmp_path, monkeypatch, "The.Fix.2026.2160p.NOW.WEB-DL.playWEB",
            lookup, details,
            ("Hotel Tehran", "2026", "movie", "https://m.media-amazon.com/h.jpg"),
        )
        desc = item.findtext("description") or ""
        assert "m.media-amazon.com" not in desc

    def test_tmdb_poster_present_omdb_not_called(self, tmp_path, monkeypatch):
        lookup = MovieInfo(
            tmdb_id=999, title="Upgrade", year="2034", media_type="movie",
            poster_url="https://image.tmdb.org/t/p/w500/p.jpg",
        )
        details = MovieInfo(imdb_id="tt1234567")

        def explode(imdb_id):
            raise AssertionError("OMDb must not be called when TMDb already has a poster")

        monkeypatch.setattr(se, "_omdb_by_id", explode)
        item = self._enrich(
            tmp_path, monkeypatch, "Upgrade.2034.2160p.WEB-DL", lookup, details, None
        )
        assert "image.tmdb.org/t/p/w500/p.jpg" in (item.findtext("description") or "")