"""IMDb/OMDb fallback searches used when TMDb's own search misses.

Both helpers must filter the returned candidates by IMDb's type tag (`qid`):
a series is spelled "tvSeries"/"tvMiniSeries", not "tv", so a filter that
expected "tv" silently skipped every one of them.
"""
from __future__ import annotations

import pytest

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