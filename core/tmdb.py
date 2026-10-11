from __future__ import annotations

import atexit
import json
import os
import re
import time
from dataclasses import dataclass

import httpx

from core.logging_utils import get_logger

logger = get_logger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"

# API calls are rate-limited to 4/sec; keep the throttle between requests.
MIN_GAP_SECONDS = 0.25

# Disk-backed result cache so a poster/year fetched today serves tomorrow's
# feed of the same movie/series without another API call. Hits live 30 days
# (posters/years are stable); negative results expire in 5 days so titles
# added to TMDb later get another chance to resolve.
TMDB_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tmdb_cache.json"
)
HIT_TTL_SECONDS = 30 * 24 * 3600
MISS_TTL_SECONDS = 5 * 24 * 3600

# Bump when MovieInfo gains a field the enricher depends on, or when the cache
# *semantics* change in a way stale entries would fight. imdb_id is why this is
# 2: entries from before it exist carry no imdb_id, and without the bump every
# cached film would keep its /find search link for the full 30-day hit TTL.
# Version 3 is the "no negative-caching on an unreachable API" bump: a schema-2
# run under rate-limit pressure stored 5-day misses for titles that DO exist,
# which froze their /find search links until the TTL. Only a 200-with-empty
# response is a definitive negative; transport and all-keys-429 failures may be
# transient, so they are never cached, and this tag throws the poisoned
# entries away.
CACHE_SCHEMA = 3

_last_request = 0.0


@dataclass
class MovieInfo:
    poster_url: str | None = None
    year: str | None = None
    title: str | None = None
    release_date: str | None = None
    tmdb_id: int | None = None
    media_type: str | None = None  # "movie" or "tv"
    # IMDb id ("tt…"), so the enricher can write a direct /title/ link instead
    # of a /find search URL. Populated from TMDb details (movie) / external_ids
    # (tv) and from find_by_imdb().
    imdb_id: str | None = None

    def _as_dict(self) -> dict:
        return {
            "poster_url": self.poster_url,
            "year": self.year,
            "title": self.title,
            "release_date": self.release_date,
            "tmdb_id": self.tmdb_id,
            "media_type": self.media_type,
            "imdb_id": self.imdb_id,
        }

    @classmethod
    def _from_dict(cls, raw: dict) -> MovieInfo:
        return cls(
            poster_url=raw.get("poster_url"),
            year=raw.get("year"),
            title=raw.get("title"),
            release_date=raw.get("release_date"),
            tmdb_id=raw.get("tmdb_id"),
            media_type=raw.get("media_type"),
            imdb_id=raw.get("imdb_id"),
        )


# In-memory caches. id_cache holds lookup results for a TMDb/IMDb id; the
# search cache stores title→result lookups so repeated searches of the same
# series/movie (multiple feeds, multiple episodes) cost one API call per run.
id_cache: dict[str, MovieInfo] = {}
search_cache: dict[str, MovieInfo] = {}

# Disk cache: {key: {"ts": 1735500000, "info": {...}}} persisted across runs.
_disk_cache: dict[str, dict] | None = None
_disk_dirty: bool = False


def _cache_key(media_type: str, tmdb_id: int) -> str:
    return f"{media_type}:{tmdb_id}"


def _normalize_search_title(title: str) -> str:
    """Fold title to a cache key: lower-case, keep only letters/digits."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _search_key(media_type: str, title: str, year: str | None = None) -> str:
    return f"search:{media_type}:{_normalize_search_title(title)}:{year or ''}"


def _cache_disabled() -> bool:
    return bool(os.environ.get("TMDB_CACHE_DISABLE"))


def _get_api_keys() -> list[str]:
    """Return available TMDb API keys, primary first.

    A second key (TMDB_API_KEY2) is used when the primary is rate-limited
    (HTTP 429), so a burst of lookups -- e.g. the one-time /find -> /title
    upgrade pass over every feed -- does not stall the run.
    """
    keys: list[str] = []
    for var in ("TMDB_API_KEY", "TMDB_API_KEY2"):
        key = os.environ.get(var)
        if key and key not in keys:
            keys.append(key)
    return keys


def _load_disk_cache() -> dict[str, dict]:
    global _disk_cache
    if _disk_cache is None:
        _disk_cache = {}
        if not _cache_disabled():
            try:
                with open(TMDB_CACHE_FILE, encoding="utf-8") as f:
                    _disk_cache = json.load(f)
            except (OSError, ValueError):
                _disk_cache = {}
    return _disk_cache


def _flush_disk_cache() -> None:
    global _disk_dirty
    if not _disk_dirty or _cache_disabled():
        return
    try:
        os.makedirs(os.path.dirname(TMDB_CACHE_FILE), exist_ok=True)
        tmp = TMDB_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_load_disk_cache(), f, ensure_ascii=False)
        os.replace(tmp, TMDB_CACHE_FILE)
    except OSError as exc:
        logger.warning("tmdb.cache_write_failed", error=str(exc))
    finally:
        _disk_dirty = False


atexit.register(_flush_disk_cache)


def _store_cache(key: str, info: MovieInfo, *, is_hit: bool, ts: float | None = None) -> None:
    """Record a result in memory and on disk (write-through, flushed at exit)."""
    global _disk_dirty
    if _cache_disabled():
        return
    id_cache[key] = info
    search_cache[key] = info

    disk = _load_disk_cache()
    disk[key] = {
        "ts": ts if ts is not None else time.time(),
        "is_hit": is_hit,
        "schema": CACHE_SCHEMA,
        "info": info._as_dict(),
    }
    _disk_dirty = True


def _lookup_cache(key: str) -> MovieInfo | None:
    """Return a fresh cached result (memory then disk), None if absent/stale."""
    if _cache_disabled():
        return None

    if key in id_cache or key in search_cache:
        info = id_cache.get(key) or search_cache.get(key)
        return info

    disk = _load_disk_cache()
    entry = disk.get(key)
    if not entry or key not in disk:
        return None
    if entry.get("schema") != CACHE_SCHEMA:
        return None

    ts = entry.get("ts") or 0
    is_hit = entry.get("is_hit", True)
    ttl = HIT_TTL_SECONDS if is_hit else MISS_TTL_SECONDS
    if time.time() - ts > ttl:
        return None

    info = MovieInfo._from_dict(entry.get("info", {}))
    search_cache[key] = info
    return info


def _rate_limit() -> None:
    global _last_request
    now = time.monotonic()
    gap = now - _last_request
    if gap < MIN_GAP_SECONDS:
        time.sleep(MIN_GAP_SECONDS - gap)
    _last_request = time.monotonic()


def _api_get(path: str, params: dict[str, str]) -> httpx.Response | None:
    """GET a TMDb endpoint, rotating to the next key on rate-limit (429).

    Returns the first non-429 response, or None when every key was
    rate-limited / errored. Callers keep their existing 404 / raise_for_status
    handling, so this only adds the key rotation.
    """
    for api_key in _get_api_keys():
        _rate_limit()
        try:
            resp = httpx.get(
                f"{TMDB_BASE}{path}",
                params={**params, "api_key": api_key},
                timeout=10,
            )
        except httpx.HTTPError:
            continue
        if resp.status_code == 429:
            logger.warning("tmdb.rate_limited", key_suffix=api_key[-4:])
            continue
        return resp
    return None


def movie_lookup(tmdb_id: int) -> MovieInfo:
    key = _cache_key("movie", tmdb_id)
    cached = _lookup_cache(key)
    if cached is not None:
        return cached

    result = _fetch("movie", tmdb_id)
    if result is None:
        # The API was unreachable or every key rate-limited. That is not a
        # negative answer, so no miss is cached (a cached miss keeps the film
        # without an IMDb link for its whole TTL).
        return MovieInfo()
    _store_cache(key, result, is_hit=bool(result.poster_url or result.year or result.title))
    return result


def tv_lookup(tmdb_id: int) -> MovieInfo:
    key = _cache_key("tv", tmdb_id)
    cached = _lookup_cache(key)
    if cached is not None:
        return cached

    result = _fetch("tv", tmdb_id)
    if result is None:
        return MovieInfo()
    _store_cache(key, result, is_hit=bool(result.poster_url or result.year or result.title))
    return result


def _tv_external_imdb(tv_id: int) -> str | None:
    """IMDb id for a series, via TMDb's external_ids endpoint.

    A series' ``/tv/{id}`` payload has no ``imdb_id`` (unlike a movie's), so the
    direct title link for a TV item needs this second call. Its own failures are
    swallowed: a missing IMDb id costs a search-style link, not the poster.
    """
    resp = _api_get(f"/tv/{tv_id}/external_ids", {})
    if resp is None:
        return None
    try:
        resp.raise_for_status()
        return (resp.json() or {}).get("imdb_id") or None
    except Exception:
        return None


def _fetch(media_type: str, tmdb_id: int, imdb_id: str | None = None) -> MovieInfo | None:
    """Details for one TMDb id; None when the API could not be reached.

    None means "no answer", never "not found": a transport failure or an
    all-keys-429 is transient and must not be turned into a cached miss. ``404``
    and an empty body are real answers and come back as an empty MovieInfo.
    """
    if not _get_api_keys():
        return None

    resp = _api_get(f"/{media_type}/{tmdb_id}", {})
    if resp is None:
        return None
    try:
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

        # The direct IMDb link needs the id, and TMDb carries it for movies in
        # the details payload and for series only under /external_ids.
        imdb = imdb_id
        if not imdb and media_type == "movie":
            imdb = data.get("imdb_id") or None
        if not imdb and media_type == "tv":
            imdb = _tv_external_imdb(tmdb_id)

        return MovieInfo(
            poster_url=poster_url,
            year=year,
            title=title,
            release_date=date_str if len(date_str) >= 4 else None,
            tmdb_id=tmdb_id,
            media_type=media_type,
            imdb_id=imdb,
        )
    except Exception as exc:
        logger.warning(
            "tmdb.lookup_failed",
            media_type=media_type,
            tmdb_id=tmdb_id,
            error=str(exc),
        )
        # The response was not usable; treat like an unreachable API so no
        # negative entry is cached for a film that may resolve next run.
        return None


def find_by_imdb(imdb_id: str) -> MovieInfo:
    """Look up a movie by IMDb ID using TMDb's find endpoint."""
    if not _get_api_keys():
        return MovieInfo()

    key = f"imdb:{imdb_id}"
    cached = _lookup_cache(key)
    if cached is not None:
        return cached

    resp = _api_get(f"/find/{imdb_id}", {"external_source": "imdb_id"})
    if resp is None:
        # Unreachable/rate-limited is not a negative answer; do not cache it.
        return MovieInfo()
    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("tmdb.imdb_find_failed", imdb_id=imdb_id, error=str(exc))
        return MovieInfo()

    results = data.get("movie_results") or data.get("tv_results") or []
    if not results:
        # A real 200 with no results is a definitive negative: cache the miss.
        empty = MovieInfo()
        _store_cache(key, empty, is_hit=False)
        return empty

    entry = results[0]
    tmdb_id = entry["id"]
    media_type = "movie" if entry.get("media_type") == "movie" or "title" in entry else "tv"
    result = _fetch(media_type, tmdb_id, imdb_id=imdb_id)
    if result is None:
        return MovieInfo()
    result.tmdb_id = tmdb_id
    result.media_type = media_type
    result.imdb_id = imdb_id
    _store_cache(key, result, is_hit=bool(result.poster_url or result.year or result.title))
    return result


def _pick_search_result(results: list[dict]) -> dict | None:
    """First search hit that actually carries a poster, else the top hit.

    TMDb regularly returns a thin partial match (no ``poster_path``) above a
    fuller one, so taking ``results[0]`` alone loses the poster for a title we
    did in fact match. Falling back to ``results[0]`` keeps the year/title
    fields usable when no hit has artwork.
    """
    if not results:
        return None
    for entry in results:
        if entry.get("poster_path"):
            return entry
    return results[0]


def search_movie(title: str, year: str | None = None) -> MovieInfo:
    """Search TMDb by title for year/poster. Optional year param narrows results."""
    if not _get_api_keys():
        return MovieInfo()

    key = _search_key("movie", title, year)
    cached = _lookup_cache(key)
    if cached is not None:
        return cached

    try:
        params: dict[str, str | int] = {"query": title}
        if year:
            params["year"] = int(year)
        resp = _api_get("/search/movie", params)
        if resp is None:
            # Unreachable/rate-limited: not a negative answer, so no miss cache.
            return MovieInfo()
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        entry = _pick_search_result(results)
        if entry is None:
            _store_cache(key, MovieInfo(), is_hit=False)
            return MovieInfo()

        tmdb_id = entry["id"]
        poster_path = entry.get("poster_path")
        poster_url = f"{TMDB_IMAGE}{poster_path}" if poster_path else None
        date_str = entry.get("release_date") or ""
        movie_year = date_str[:4] if len(date_str) >= 4 else None
        movie_title = (entry.get("title") or "").strip() or None

        result = MovieInfo(
            poster_url=poster_url,
            year=movie_year,
            title=movie_title,
            release_date=date_str if len(date_str) >= 4 else None,
            tmdb_id=tmdb_id,
            media_type="movie",
        )
        # Deliberately *not* stored under the id key. A search hit carries no
        # imdb_id (the endpoint does not return one), and the enricher's
        # follow-up movie_lookup() must reach _fetch() to learn it -- caching the
        # partial record under the id key made that lookup a no-op and left
        # every searched film with a /find link instead of /title/tt…/.
        _store_cache(key, result, is_hit=True)
        return result
    except Exception as exc:
        logger.warning("tmdb.search_failed", title=title, error=str(exc))
        return MovieInfo()


def search_tv(title: str, year: str | None = None) -> MovieInfo:
    """Search TMDb by title for a TV series year/poster (mirrors search_movie)."""
    if not _get_api_keys():
        return MovieInfo()

    key = _search_key("tv", title, year)
    cached = _lookup_cache(key)
    if cached is not None:
        return cached

    try:
        params: dict[str, str | int] = {"query": title}
        if year:
            # /search/tv filters by first air year via first_air_date_year.
            params["first_air_date_year"] = int(year)
        resp = _api_get("/search/tv", params)
        if resp is None:
            # Unreachable/rate-limited: not a negative answer, so no miss cache.
            return MovieInfo()
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        entry = _pick_search_result(results)
        if entry is None:
            _store_cache(key, MovieInfo(), is_hit=False)
            return MovieInfo()

        tmdb_id = entry["id"]
        poster_path = entry.get("poster_path")
        poster_url = f"{TMDB_IMAGE}{poster_path}" if poster_path else None
        date_str = entry.get("first_air_date") or ""
        series_year = date_str[:4] if len(date_str) >= 4 else None
        series_title = (entry.get("name") or "").strip() or None

        result = MovieInfo(
            poster_url=poster_url,
            year=series_year,
            title=series_title,
            release_date=date_str if len(date_str) >= 4 else None,
            tmdb_id=tmdb_id,
            media_type="tv",
        )
        # Same reasoning as search_movie: a series' imdb_id lives under
        # /tv/{id}/external_ids, so the search hit must not shadow the details
        # lookup that fetches it.
        _store_cache(key, result, is_hit=True)
        return result
    except Exception as exc:
        logger.warning("tmdb.search_tv_failed", title=title, error=str(exc))
        return MovieInfo()


def poster_for_movie(tmdb_id: int) -> str | None:
    return movie_lookup(tmdb_id).poster_url


def poster_for_tv(tmdb_id: int) -> str | None:
    return tv_lookup(tmdb_id).poster_url