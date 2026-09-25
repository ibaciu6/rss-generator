"""Streaming site enrichment (movies, episodes, series)."""
from __future__ import annotations

import csv
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import httpx

from core.tmdb import find_by_imdb, movie_lookup, search_movie, search_tv, tv_lookup

FEEDS_DIR = Path(__file__).resolve().parent.parent.parent / "feeds"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# EpGuides publishes a TSV of every tracked series (title, directory/slug, …).
# We mirror it locally so enrich runs without a network round-trip per feed.
EPGUIDES_URL = "https://epguides.com/common/allshows.txt"
EPGUIDES_CACHE = REPO_ROOT / "data" / "allshows.txt"
EPGUIDES_TTL_SECONDS = 7 * 24 * 3600
# EpGuides' own site search is a Google Custom Search. Used as a fallback so
# every TV item still links to EpGuides when a series is absent from allshows.txt.
EPGUIDES_CSE_URL = "https://www.google.com/cse"
EPGUIDES_CSE_CX = "006364566242780170875:hrcq-leun10"

# Matches /movie/ID, /movie/slug/ID, /movie/ID-slug (and same for /tv/)
TMDB_ID_RE = re.compile(r"/(movie|tv)(?:/[^/]+)?/(\d{4,})(?:/|$|-)")
# Matches URLs with a year in them (e.g. "the-box-2026")
URL_YEAR_RE = re.compile(r"-(19\d{2}|20\d{2})(?:-|/)")
IMDB_ID_RE = re.compile(r"(tt\d{7,8})")
IMG_TAG_RE = re.compile(r'<img\s[^>]*>', re.IGNORECASE)
# Torrent episode titles carry SxxEyy (e.g. "The Gentlemen 2024 S02E03 …") or NxM
# signal that a title is a TV series, so we look up TMDb /search/tv, not movies.
EPISODE_TITLE_RE = re.compile(r"\bS\d{1,2}\s*E\d{1,2}\b|\b\d+x\d+\b", re.IGNORECASE)
HAS_YEAR_RE = re.compile(r"\(\d{4}\)")
HAS_BARE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
YEAR_STRIP_RE = re.compile(r"[\(\[\{]\d{4}[\)\]\}]")
NON_WORD_RE = re.compile(r"[^\w\s]+")

# Torrent/release-group noise to strip from titles before TMDB search.
SEARCH_NOISE_RE = re.compile(
    r"\[[^\]]*\]"
    r"|\b(?:1080p|720p|2160p|4k|8k|uhd|"
    r"bluray|brrip|web-?dl|webrip|hdrip|hdtv|"
    r"dvdrip|dvdscr|remux|camrip|bdrip|bdr|"
    r"pdtv|dsr|ppv|dvb|iptv|sdtv|tvrip|vhsrip|"
    r"x264|x265|h\.?264|h\.?265|h\s+264|h\s+265|264|265|avc|hevc|av1|vp9|vp8|vc1|"
    r"mpeg2|mpeg4|xvid|divx|"
    r"ddp5?\.?1?|dd5|dts-?hd|dts|eac3|ac3|flac|aac|atmos|"
    r"true-?hd|lpcm|pcm|mp3|opus|ogg|vorbis|alac|wma|wav|aiff|ape|"
    r"dolby|digital|plus\d*|"
    r"mp4|mkv|avi|webm|m2ts|wmv|flv|"
    r"hdr10?(?:plus)?|hlg|sdr|hdr|dd|dv|dovi|ma\b|p7|"
    r"5\.1|7\.1|2\.0|\d+bit|multi|dual|nordic|"
    r"\d+\s*(?:gb|tb|mb|kb|hrs?|h|min|mins?)\b|"
    r"\d+\s*-\s*\d+\b|"
    r"cinephiles|narchives|someone|btm\b|"
    r"dsnp|dnsp|osn|web|hmax|hulu|atvp|atv|peacock|para|itunes|it|"
    r"multiaudios?|arsub|multisubs?|"
    r"rerip|readnfo|internal|extended|unrated|"
    r"complete|retail|proper|repack|amzn|nf|"
    r"imax|sbs|interlaced|progressive|openmatte|anamorphic|hybrid|"
    r"pal|ntsc|lbxd|uhd|blu-?ray|blu|ray|\bxd|\bscene|"
    r"telesync|telecine|cam\b|ts\b|hc\b|werk|dvd\b|remaster|remuxed|"
    r"doxxi|nosecret|layer|dual\b|disc)"
    r"|\b5\s*1\b|\b7\s*1\b"
    r"|\s*-\s*[A-Za-z0-9]+\s*$",
    re.IGNORECASE,
)


def _clean_search_title(raw: str) -> str:
    """Strip torrent release-group noise so TMDB search gets a clean movie name."""
    t = re.sub(r"\[[^\]]*\]", " ", raw)
    t = re.sub(r"[()]", " ", t)
    t = re.sub(r"[._]+", " ", t)
    t = re.sub(r"\b(?:19|20)\d{2}\b", " ", t)
    t = SEARCH_NOISE_RE.sub(" ", t)
    t = re.sub(r"\s*-\s*[A-Za-z0-9]+\s*$", " ", t)
    t = re.sub(r"\s*~+\s*[A-Za-z0-9]*\s*$", " ", t)
    t = re.sub(r"[-\s]+", " ", t).strip()
    while True:
        m = re.search(r"\s+(\S)$", t)
        if m:
            t = t[:m.start()].rstrip()
        else:
            break
    return t.strip()


def _normalize_epguides_title(title: str) -> str:
    """Fold punctuation/spaces/case so scene names match EpGuides titles."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _epguides_series_title(title: str) -> str:
    """Extract the series name from an episode title."""
    t = YEAR_STRIP_RE.sub(" ", title)
    m = EPISODE_TITLE_RE.search(t)
    if m:
        t = t[: m.start()]
    t = re.sub(r"\b(?:19|20)\d{2}\b\s*$", " ", t)
    t = re.sub(r"[\s\-–—:|.,]+$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _load_epguides(path: Path = EPGUIDES_CACHE) -> dict[str, str]:
    """Load EpGuides allshows.txt → {normalized title: directory slug}."""
    mapping: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                title = (row.get("title") or "").strip()
                slug = (row.get("directory") or "").strip()
                if title and slug:
                    mapping.setdefault(_normalize_epguides_title(title), slug)
    except OSError:
        return mapping
    return mapping


def _epguides_mapping() -> dict[str, str]:
    """Return the EpGuides title→slug map, refreshing the local mirror when stale."""
    cache = EPGUIDES_CACHE
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < EPGUIDES_TTL_SECONDS:
            return _load_epguides(cache)
    if os.environ.get("EPGUIDES_DISABLE_DOWNLOAD"):
        return _load_epguides(cache) if cache.exists() else {}
    try:
        resp = httpx.get(EPGUIDES_URL, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(resp.content)
        return _load_epguides(cache)
    except Exception:
        return _load_epguides(cache) if cache.exists() else {}


_EPGUIDES_MAP_CACHE: dict[str, str] | None = None


def _epguides_map() -> dict[str, str]:
    """Process-wide cached EpGuides map (downloaded once, reused across feeds)."""
    global _EPGUIDES_MAP_CACHE
    if _EPGUIDES_MAP_CACHE is None:
        _EPGUIDES_MAP_CACHE = _epguides_mapping()
    return _EPGUIDES_MAP_CACHE


def _find_epguides_slug(series_title: str, mapping: dict[str, str]) -> str | None:
    """Look up an EpGuides page slug for a series name."""
    if not mapping or not series_title:
        return None
    candidates = [series_title]
    stripped = re.sub(r"^the\s+", "", series_title, flags=re.IGNORECASE)
    if stripped != series_title:
        candidates.append(stripped)
    for candidate in candidates:
        n = _normalize_epguides_title(candidate)
        if n in mapping:
            return mapping[n]
    return None


def _build_epguides_link(slug: str) -> str:
    """Build the EpGuides link matching the existing trailer/IMDb link style."""
    return (
        f'<br><a href="https://epguides.com/{slug}/" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">EpGuides</b></a>'
    )


def _build_epguides_search_link(series_title: str) -> str:
    """Fallback link: EpGuides' own site search for a series with no known page."""
    q = quote(series_title, safe="")
    cx = quote(EPGUIDES_CSE_CX, safe="")
    return (
        f'<br><a href="{EPGUIDES_CSE_URL}?cx={cx}&amp;q={q}" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">EpGuides</b></a>'
    )


def _attach_epguides_link(
    item: ET.Element,
    series_title: str,
    mapping: dict[str, str],
    *,
    allow_fallback: bool = False,
) -> bool:
    """Append the EpGuides link to an item's description/encoded."""
    cleaned = _epguides_series_title(series_title)
    slug = _find_epguides_slug(cleaned, mapping) if mapping else None
    if slug:
        eg_link = _build_epguides_link(slug)
    elif allow_fallback and cleaned:
        eg_link = _build_epguides_search_link(cleaned)
    else:
        return False
    target = item.find("description")
    if target is None:
        target = ET.SubElement(item, "description")
        target.text = ""
    added = False
    if "EpGuides</b>" not in (target.text or ""):
        target.text = (target.text or "") + eg_link
        added = True
    encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if encoded is not None and "EpGuides</b>" not in (encoded.text or ""):
        encoded.text = (encoded.text or "") + eg_link
        added = True
    return added


def _feed_kinds() -> dict[str, str]:
    """Map feed filename → site config ``kind`` ("movie"/"series") when declared."""
    try:
        from core.config import load_config
    except ImportError:
        return {}

    config_path = REPO_ROOT / "config" / "sites.yaml"
    if not config_path.exists():
        return {}

    config = load_config(config_path)
    return {site.feed_file: site.kind for site in config.sites if site.kind}


def _build_imdb_link(title: str, year: str | None) -> str:
    """Build an IMDb search link."""
    query = f"{title} ({year})" if year else title
    q = quote(query, safe="")
    return (
        f'<a href="https://www.imdb.com/find?q={q}&amp;s=tt" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">IMDb</b></a>'
    )


def _build_trailer_link(title: str, year: str | None) -> str:
    """Build a YouTube trailer search link."""
    query = f"{title} ({year})" if year else title
    q = quote(query, safe="")
    return (
        f'<a href="https://www.youtube.com/results?search_query='
        f'{q}+preview%7Cpromo%7Ctrailer+-fake+-fan&amp;sp=EgIYAQ%253D%253D" '
        f'target="_blank" rel="noopener noreferrer">'
        f'<b style="color:#6600cc;">Trailer</b></a>'
    )


def _title_matches(tmdb_title: str, feed_title: str) -> bool:
    """Check if TMDb title validates against feed title."""
    if not tmdb_title or not feed_title:
        return True
    a = NON_WORD_RE.sub("", tmdb_title).strip().lower()
    b = NON_WORD_RE.sub("", feed_title).strip().lower()
    b = YEAR_STRIP_RE.sub("", b).strip()
    if not a or not b:
        return True
    return a in b or b in a or any(w in b for w in a.split() if len(w) > 3)


def _extract_tmdb_id(link: str) -> tuple[str, int] | None:
    m = TMDB_ID_RE.search(link)
    if m:
        return (m.group(1), int(m.group(2)))
    return None


def _lookup(media_type: str, tmdb_id: int):
    if media_type == "movie":
        return movie_lookup(tmdb_id)
    return tv_lookup(tmdb_id)


def _lookup_link(link: str):
    """Try TMDb ID first, then IMDb ID fallback."""
    id_info = _extract_tmdb_id(link)
    if id_info:
        return _lookup(*id_info)
    m = IMDB_ID_RE.search(link)
    if m:
        return find_by_imdb(m.group(1))
    return None


def process_feed(
    path: Path,
    epguides_mapping: dict[str, str] | None = None,
    epguides_misses: dict | None = None,
    is_series_feed: bool | None = None,
) -> tuple[bool, dict]:
    """Process a single feed file. Returns (changed, stats)."""
    stats: dict = {"items": 0, "posters": 0, "years": 0, "future": 0, "errors": 0, "skipped": 0, "links": 0, "epguides": 0}
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        stats["errors"] = 1
        return False, stats

    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        stats["errors"] = 1
        return False, stats

    changed = False

    for item in channel.findall("item"):
        stats["items"] += 1
        link_el = item.find("link")
        if link_el is None or not link_el.text:
            stats["skipped"] += 1
            continue

        title_el = item.find("title")
        title_text = title_el.text.strip() if title_el is not None and title_el.text else ""
        has_year = bool(HAS_YEAR_RE.search(title_text))
        has_bare_year = bool(HAS_BARE_YEAR_RE.search(title_text))

        # TV episode titles link to the series' EpGuides page.
        is_tv = (
            bool(EPISODE_TITLE_RE.search(title_text))
            if is_series_feed is None
            else is_series_feed
        )
        series_search_title = _epguides_series_title(title_text) if is_tv and title_text else ""
        epguides_slug = None
        if series_search_title and epguides_mapping:
            epguides_slug = _find_epguides_slug(series_search_title, epguides_mapping)
        is_miss = (
            is_tv and series_search_title and not epguides_slug
            and "EpGuides</b>" not in (item.findtext("description", "") or "")
        )
        if is_miss and epguides_misses is not None:
            epguides_misses.setdefault(path, []).append(item)
        elif epguides_slug and _attach_epguides_link(item, series_search_title, epguides_mapping):
            stats["epguides"] += 1
            changed = True

        # Already-enriched items need no further TMDb lookups.
        existing_enriched = item.findtext("description", "") or ""
        if "image.tmdb.org" in existing_enriched and "www.imdb.com/find?" in existing_enriched:
            continue

        info = _lookup_link(link_el.text)
        if info is None:
            if title_text:
                search_title = _clean_search_title(title_text)
                year_from_url = None
                url_year_match = URL_YEAR_RE.search(link_el.text)
                if url_year_match:
                    year_from_url = url_year_match.group(1)
                if is_tv:
                    search_title = EPISODE_TITLE_RE.sub(" ", search_title)
                    search_title = re.sub(r"\s+", " ", search_title).strip()
                info = (search_tv if is_tv else search_movie)(search_title, year=year_from_url)
                if not info or not info.poster_url:
                    stats["skipped"] += 1
                    continue
            else:
                stats["skipped"] += 1
                continue

        if info.title and title_text and not _title_matches(info.title, title_text):
            stats["skipped"] += 1
            continue

        has_year = bool(HAS_YEAR_RE.search(title_text))
        has_bare_year = bool(HAS_BARE_YEAR_RE.search(title_text))

        if info.year and not has_year and not has_bare_year and title_text:
            title_el.text = f"{title_text} ({info.year})"
            stats["years"] += 1
            changed = True

        # Skip poster replacement if img already from TMDB
        desc_el = item.find("description")
        skip_poster = False
        if desc_el is not None and desc_el.text:
            existing_img = IMG_TAG_RE.search(desc_el.text)
            if existing_img and "image.tmdb.org" in existing_img.group(0):
                skip_poster = True

        existing_desc = desc_el.text if desc_el is not None else ""
        already_linked = "www.imdb.com/find?" in (existing_desc or "")

        if info.poster_url and not skip_poster:
            link_title = info.title or title_text
            link_block = ""
            if link_title and not already_linked:
                link_title = YEAR_STRIP_RE.sub("", link_title).strip()
                link_block = (
                    "<br>"
                    + _build_trailer_link(link_title, info.year)
                    + "<br>"
                    + _build_imdb_link(link_title, info.year)
                )
            for tag in ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"]:
                el = item.find(tag)
                if el is not None:
                    if el.text:
                        old = IMG_TAG_RE.search(el.text)
                        if old:
                            style = re.search(r'style\s*=\s*"([^"]*)"', old.group(0))
                            style_attr = f' style="{style.group(1)}"' if style else ''
                            img_html = f'<img src="{info.poster_url}"{style_attr}>'
                            el.text = IMG_TAG_RE.sub(img_html, el.text)
                            if link_block:
                                after = IMG_TAG_RE.search(el.text)
                                if after:
                                    el.text = el.text[:after.end()] + link_block + el.text[after.end():]
                        else:
                            el.text = f'<img src="{info.poster_url}">' + link_block + "<br>" + el.text
                    else:
                        el.text = f'<img src="{info.poster_url}">' + link_block
                else:
                    desc = ET.SubElement(item, "description")
                    desc.text = f'<img src="{info.poster_url}">' + link_block
            stats["posters"] += 1
            if link_block:
                stats["links"] += 1
            changed = True

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return changed, stats


def main():
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        print("SKIP  TMDB_API_KEY not set — skipping enrichment")
        return

    xml_files = sorted(FEEDS_DIR.glob("*.xml"))
    total_feeds = len(xml_files)
    feed_kinds = _feed_kinds()
    epguides_mapping = _epguides_map()
    if epguides_mapping:
        print(f"  EpGuides map: {len(epguides_mapping)} series")
    enriched = 0
    total_items = 0
    total_posters = 0
    total_years = 0
    total_skipped = 0
    total_links = 0
    total_epguides = 0
    epguides_misses: dict[Path, list[ET.Element]] = {}

    for idx, path in enumerate(xml_files, 1):
        feed_name = path.stem
        print(f"  [{idx}/{total_feeds}] {feed_name}... ", end="", flush=True)

        kind = feed_kinds.get(path.name)
        is_series_feed = None if kind is None else kind == "series"
        changed, stats = process_feed(
            path,
            epguides_mapping=epguides_mapping,
            epguides_misses=epguides_misses,
            is_series_feed=is_series_feed,
        )

        total_items += stats["items"]
        total_posters += stats["posters"]
        total_years += stats["years"]
        total_skipped += stats["skipped"]
        total_links += stats.get("links", 0)
        total_epguides += stats.get("epguides", 0)

        if changed:
            enriched += 1
        status = "OK" if changed else "--"
        parts = f"items={stats['items']}"
        if stats["posters"]:
            parts += f" posters={stats['posters']}"
        if stats["years"]:
            parts += f" years={stats['years']}"
        if stats.get("links"):
            parts += f" links={stats['links']}"
        if stats.get("epguides"):
            parts += f" epguides={stats['epguides']}"
        if stats["skipped"]:
            parts += f" skipped={stats['skipped']}"
        if stats["errors"]:
            parts += f" ERRORS={stats['errors']}"
        print(f"[{status}] {parts}")

    if epguides_misses:
        unmatched = sum(len(items) for items in epguides_misses.values())
        print(f"  EpGuides: {unmatched} TV item(s) in {len(epguides_misses)} feed(s) unresolved")

    summary = f"Enriched {enriched}/{total_feeds} feeds | {total_items} items"
    if total_posters:
        summary += f" | {total_posters} posters"
    if total_years:
        summary += f" | {total_years} years added"
    if total_skipped:
        summary += f" | {total_skipped} skipped"
    if total_links:
        summary += f" | {total_links} links added"
    if total_epguides:
        summary += f" | {total_epguides} epguides"
    print(summary)