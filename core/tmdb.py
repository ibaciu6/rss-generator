from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from core.logging_utils import get_logger

logger = get_logger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"

_last_request = 0.0


@dataclass
class MovieInfo:
    poster_url: str | None = None
    year: str | None = None
    title: str | None = None
    release_date: str | None = None


_cache: dict[str, MovieInfo] = {}


def _cache_key(media_type: str, tmdb_id: int) -> str:
    return f"{media_type}:{tmdb_id}"


def _get_api_key() -> str | None:
    return os.environ.get("TMDB_API_KEY") or None


def movie_lookup(tmdb_id: int) -> MovieInfo:
    key = _cache_key("movie", tmdb_id)
    if key in _cache:
        return _cache[key]

    result = _fetch("movie", tmdb_id)
    _cache[key] = result
    return result


def tv_lookup(tmdb_id: int) -> MovieInfo:
    key = _cache_key("tv", tmdb_id)
    if key in _cache:
        return _cache[key]

    result = _fetch("tv", tmdb_id)
    _cache[key] = result
    return result


def _fetch(media_type: str, tmdb_id: int) -> MovieInfo:
    api_key = _get_api_key()
    if api_key is None:
        return MovieInfo()

    global _last_request
    now = time.monotonic()
    gap = now - _last_request
    if gap < 0.25:
        time.sleep(0.25 - gap)
    _last_request = time.monotonic()

    try:
        resp = httpx.get(
            f"{TMDB_BASE}/{media_type}/{tmdb_id}",
            params={"api_key": api_key},
            timeout=10,
        )
        if resp.status_code == 404:
            return MovieInfo()
        resp.raise_for_status()
        data = resp.json()

        poster_url: str | None = None
        poster = data.get("poster_path")
        if poster:
            poster_url = f"{TMDB_IMAGE}{poster}"

        year: str | None = None
        date_key = "release_date" if media_type == "movie" else "first_air_date"
        date_str = data.get(date_key) or ""
        if len(date_str) >= 4:
            year = date_str[:4]

        title: str | None = None
        raw = data.get("title") or data.get("name")
        if raw:
            title = raw.strip()

        return MovieInfo(
            poster_url=poster_url,
            year=year,
            title=title,
            release_date=date_str if len(date_str) >= 4 else None,
        )
    except Exception as exc:
        logger.warning(
            "tmdb.lookup_failed",
            media_type=media_type,
            tmdb_id=tmdb_id,
            error=str(exc),
        )
        return MovieInfo()


def find_by_imdb(imdb_id: str) -> MovieInfo:
    """Look up a movie by IMDb ID using TMDb's find endpoint."""
    api_key = _get_api_key()
    if api_key is None:
        return MovieInfo()

    key = f"imdb:{imdb_id}"
    if key in _cache:
        return _cache[key]

    global _last_request
    now = time.monotonic()
    gap = now - _last_request
    if gap < 0.25:
        time.sleep(0.25 - gap)
    _last_request = time.monotonic()

    try:
        resp = httpx.get(
            f"{TMDB_BASE}/find/{imdb_id}",
            params={"api_key": api_key, "external_source": "imdb_id"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("movie_results") or data.get("tv_results") or []
        if not results:
            _cache[key] = MovieInfo()
            return _cache[key]

        entry = results[0]
        tmdb_id = entry["id"]
        media_type = "movie" if entry.get("media_type") == "movie" or "title" in entry else "tv"
        result = _fetch(media_type, tmdb_id)
        _cache[key] = result
        return result
    except Exception as exc:
        logger.warning("tmdb.imdb_find_failed", imdb_id=imdb_id, error=str(exc))
        _cache[key] = MovieInfo()
        return _cache[key]


def search_movie(title: str, year: str | None = None) -> MovieInfo:
    """Search TMDb by title for year/poster. Optional year param narrows results."""
    api_key = _get_api_key()
    if api_key is None:
        return MovieInfo()

    global _last_request
    now = time.monotonic()
    gap = now - _last_request
    if gap < 0.25:
        time.sleep(0.25 - gap)
    _last_request = time.monotonic()

    try:
        params: dict[str, str | int] = {"api_key": api_key, "query": title}
        if year:
            params["year"] = int(year)
        resp = httpx.get(
            f"{TMDB_BASE}/search/movie",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return MovieInfo()

        entry = results[0]
        tmdb_id = entry["id"]
        poster_path = entry.get("poster_path")
        poster_url = f"{TMDB_IMAGE}{poster_path}" if poster_path else None
        date_str = entry.get("release_date") or ""
        movie_year = date_str[:4] if len(date_str) >= 4 else None
        movie_title = (entry.get("title") or "").strip() or None

        _cache[_cache_key("movie", tmdb_id)] = MovieInfo(
            poster_url=poster_url,
            year=movie_year,
            title=movie_title,
            release_date=date_str if len(date_str) >= 4 else None,
        )
        return MovieInfo(
            poster_url=poster_url,
            year=movie_year,
            title=movie_title,
            release_date=date_str if len(date_str) >= 4 else None,
        )
    except Exception as exc:
        logger.warning("tmdb.search_failed", title=title, error=str(exc))
        return MovieInfo()


def poster_for_movie(tmdb_id: int) -> str | None:
    return movie_lookup(tmdb_id).poster_url


def poster_for_tv(tmdb_id: int) -> str | None:
    return tv_lookup(tmdb_id).poster_url
