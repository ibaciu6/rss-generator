from __future__ import annotations

import time
from unittest.mock import patch

import core.tmdb as tmdb


def _fake_api_response(movie_id: int = 1, title: str = "The Mummy"):
    """Build a minimal TMDb /search/movie or /movie/{id} JSON payload."""
    return {
        "id": movie_id,
        "title": title,
        "name": title,
        "poster_path": "/abc.jpg",
        "release_date": "2026-03-01",
        "first_air_date": None,
        "results": [{"id": movie_id, "title": title, "name": title, "poster_path": "/abc.jpg", "release_date": "2026-03-01", "first_air_date": None}],
    }


def _configure_httpx_mock(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.raise_for_status = lambda: None
    mock_get.return_value.json = lambda: _fake_api_response()
    return mock_get


def _reset_caches():
    tmdb.id_cache.clear()
    tmdb.search_cache.clear()
    tmdb._disk_cache = None


def test_search_movie_uses_in_memory_cache(monkeypatch):
    """Second identical search must not hit TMDb again within the same run."""
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        info1 = tmdb.search_movie("The Mummy")
        info2 = tmdb.search_movie("The Mummy")
    assert info1.poster_url and info1.poster_url.endswith("abc.jpg")
    assert mock_get.call_count == 1
    assert info2 == info1


def test_search_tv_cached_separately_from_movie(monkeypatch):
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        tmdb.search_tv("The Gentlemen")
        tmdb.search_tv("The Gentlemen")
        tmdb.search_movie("The Gentlemen")
    assert mock_get.call_count == 2


def test_miss_results_cached_but_expire_sooner(monkeypatch):
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    with patch("core.tmdb.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json = lambda: {"results": []}
        one = tmdb.search_movie("No Such Flick 9999")
        two = tmdb.search_movie("No Such Flick 9999")
    assert one.poster_url is None and two.poster_url is None
    assert mock_get.call_count == 1


def test_disk_cache_persists_across_process_boundaries(monkeypatch, tmp_path):
    """Using the API once then wiping memory still returns the cached result."""
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    cache_file = tmp_path / "tmdb_cache.json"
    monkeypatch.setattr(tmdb, "TMDB_CACHE_FILE", str(cache_file))

    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        tmdb.search_movie("The Mummy")
    tmdb._flush_disk_cache()
    assert cache_file.exists()

    _reset_caches()
    with patch("core.tmdb.httpx.get") as mock_get:
        info = tmdb.search_movie("The Mummy")
    assert mock_get.call_count == 0
    assert info.poster_url and info.poster_url.endswith("abc.jpg")


def test_hit_cache_expires_after_ttl(monkeypatch, tmp_path):
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    cache_file = tmp_path / "tmdb_cache.json"
    monkeypatch.setattr(tmdb, "TMDB_CACHE_FILE", str(cache_file))

    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        tmdb.search_movie("The Mummy")
    tmdb._flush_disk_cache()

    # Age the cached entry past the hit TTL, then the API must be hit again.
    disk = tmdb._load_disk_cache()
    for entry in disk.values():
        entry["ts"] = time.time() - (tmdb.HIT_TTL_SECONDS + 1)
    tmdb._disk_dirty = True
    tmdb._flush_disk_cache()
    _reset_caches()

    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        info = tmdb.search_movie("The Mummy")
    assert mock_get.call_count == 1


def test_cache_disabled_guard(monkeypatch):
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    monkeypatch.setenv("TMDB_CACHE_DISABLE", "1")
    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        tmdb.search_movie("The Mummy")
        tmdb.search_movie("The Mummy")
    assert mock_get.call_count == 2