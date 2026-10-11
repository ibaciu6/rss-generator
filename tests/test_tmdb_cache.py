from __future__ import annotations

import time
from unittest.mock import patch

import httpx

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
    tmdb._disk_dirty = False


def test_search_movie_uses_in_memory_cache(monkeypatch, tmp_path):
    """Second identical search must not hit TMDb again within the same run."""
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    cache_file = tmp_path / "tmdb_cache.json"
    monkeypatch.setattr(tmdb, "TMDB_CACHE_FILE", str(cache_file))
    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        info1 = tmdb.search_movie("The Mummy")
        info2 = tmdb.search_movie("The Mummy")
    assert info1.poster_url and info1.poster_url.endswith("abc.jpg")
    assert mock_get.call_count == 1
    assert info2 == info1


def test_search_tv_cached_separately_from_movie(monkeypatch, tmp_path):
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    cache_file = tmp_path / "tmdb_cache.json"
    monkeypatch.setattr(tmdb, "TMDB_CACHE_FILE", str(cache_file))
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
        _info = tmdb.search_movie("The Mummy")
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


class _FakeResp:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)
        return None

    def json(self):
        return self._payload


def test_api_get_rotates_key_on_rate_limit(monkeypatch):
    """A 429 on the primary key must retry the request with the second key."""
    _reset_caches()
    monkeypatch.setattr(tmdb, "MIN_GAP_SECONDS", 0)
    monkeypatch.setattr(tmdb, "_get_api_keys", lambda: ["k1", "k2"])
    used: list[str] = []
    resp200 = _FakeResp(200, {"id": 1})

    def fake_get(url, params=None, timeout=None):
        used.append(params["api_key"])
        return _FakeResp(429) if len(used) == 1 else resp200

    monkeypatch.setattr(tmdb.httpx, "get", fake_get)
    resp = tmdb._api_get("/movie/1", {})
    assert resp is resp200
    assert used == ["k1", "k2"]


def test_api_get_returns_none_when_every_key_rate_limited(monkeypatch):
    _reset_caches()
    monkeypatch.setattr(tmdb, "MIN_GAP_SECONDS", 0)
    monkeypatch.setattr(tmdb, "_get_api_keys", lambda: ["k1", "k2"])
    monkeypatch.setattr(tmdb.httpx, "get", lambda *a, **k: _FakeResp(429))
    assert tmdb._api_get("/movie/1", {}) is None


def test_old_schema_disk_entry_is_a_miss(monkeypatch, tmp_path):
    """A cached entry from before imdb_id existed must be re-fetched.

    CI persists data/tmdb_cache.json across runs; those stale entries carry no
    imdb_id, so a details lookup that trusts them never learns the id and every
    film keeps its /find link. The schema tag on the entry is what turns them
    into misses.
    """
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    cache_file = tmp_path / "tmdb_cache.json"
    monkeypatch.setattr(tmdb, "TMDB_CACHE_FILE", str(cache_file))

    with patch("core.tmdb.httpx.get") as mock_get:
        _configure_httpx_mock(mock_get)
        tmdb.movie_lookup(1)
    tmdb._flush_disk_cache()

    # Downgrade the freshly written entry to the pre-imdb_id schema and persist.
    disk = tmdb._load_disk_cache()
    for entry in disk.values():
        entry["schema"] = 1
    tmdb._disk_dirty = True
    tmdb._flush_disk_cache()
    _reset_caches()

    with patch("core.tmdb.httpx.get") as mock_get:
        mock_get.return_value = _FakeResp(200, _fake_api_response())
        tmdb.movie_lookup(1)
    assert mock_get.call_count == 1


def _route_details_imdb(mock_get, movie_id: int, imdb_id: str):
    """Serve /search/movie without an imdb_id and /movie/{id} with one."""
    def fake_get(url, params=None, timeout=None):
        if "/search/movie" in url:
            payload = {
                "results": [
                    {"id": movie_id, "title": "The Mummy", "poster_path": "/a.jpg",
                     "release_date": "2026-03-01", "first_air_date": None}
                ]
            }
        else:
            payload = _fake_api_response(movie_id)
            payload["imdb_id"] = imdb_id
        return _FakeResp(200, payload)

    mock_get.side_effect = fake_get


def test_search_hit_does_not_shadow_details_lookup(monkeypatch, tmp_path):
    """movie_lookup() after a search must still reach the details endpoint.

    A search result carries no imdb_id. Caching it under the id key turned the
    enricher's follow-up movie_lookup() into a no-op, so it never learned the id
    and every searched film shipped a /find link instead of /title/tt…/.
    """
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    cache_file = tmp_path / "tmdb_cache.json"
    monkeypatch.setattr(tmdb, "TMDB_CACHE_FILE", str(cache_file))

    with patch("core.tmdb.httpx.get") as mock_get:
        _route_details_imdb(mock_get, movie_id=1, imdb_id="tt1234567")
        info = tmdb.search_movie("The Mummy")
        assert info.imdb_id is None  # the search endpoint has none to give
        details = tmdb.movie_lookup(info.tmdb_id)
    assert details.imdb_id == "tt1234567"
    # The details call actually happened: search alone must not have warmed it.
    assert mock_get.call_count == 2


def test_tv_details_fetch_uses_external_ids(monkeypatch, tmp_path):
    """A series' imdb_id lives under /tv/{id}/external_ids, not the details."""
    _reset_caches()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    cache_file = tmp_path / "tmdb_cache.json"
    monkeypatch.setattr(tmdb, "TMDB_CACHE_FILE", str(cache_file))
    seen: list[str] = []

    def fake_get(url, params=None, timeout=None):
        seen.append(url)
        if url.endswith("/external_ids"):
            return _FakeResp(200, {"imdb_id": "tt9876543"})
        payload = _fake_api_response(2)
        payload.pop("imdb_id", None)
        return _FakeResp(200, payload)

    with patch("core.tmdb.httpx.get") as mock_get:
        mock_get.side_effect = fake_get
        info = tmdb.tv_lookup(2)
    assert info.imdb_id == "tt9876543"
    assert any(u.endswith("/external_ids") for u in seen)