#!/usr/bin/env python3
"""Enrich feed items with TMDb posters and years when IDs are found in links."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from core.tmdb import movie_lookup, tv_lookup, find_by_imdb, search_movie

FEEDS_DIR = Path(__file__).resolve().parent.parent / "feeds"

# Matches /movie/ID, /movie/slug/ID, /movie/ID-slug (and same for /tv/)
TMDB_ID_RE = re.compile(r"/(movie|tv)(?:/[^/]+)?/(\d{4,})(?:/|$|-)")
IMDB_ID_RE = re.compile(r"(tt\d{7,8})")
IMG_TAG_RE = re.compile(r'<img\s[^>]*>', re.IGNORECASE)
HAS_YEAR_RE = re.compile(r"\(\d{4}\)")
YEAR_STRIP_RE = re.compile(r"[\(\[\{]\d{4}[\)\]\}]")
NON_WORD_RE = re.compile(r"[^\w\s]+")

# Torrent/release-group noise to strip from titles before TMDB search.
# Quality tags, container info, codec names, and scene-group suffixes all
# pollute the query and cause TMDB to return no results.
SEARCH_NOISE_RE = re.compile(
    r"\[[^\]]*\]"                          # [1080p] [BluRay] [5.1]
    r"|\b(?:1080p|720p|2160p|4k|8k|uhd|"
    r"bluray|brrip|web-?dl|webrip|hdrip|hdtv|"
    r"dvdrip|dvdscr|remux|camrip|bdrip|bdr|"
    r"pdtv|dsr|ppv|dvb|iptv|sdtv|tvrip|vhsrip|"
    r"x26[45]|h\.?26[45]|h\s+26[45]|26[45]|avc|hevc|av1|vp9|vp8|vc1|"
    r"mpeg2|mpeg4|xvid|divx|"
    r"ddp5?\.?1?|dts-?hd|dts|eac3|ac3|flac|aac|atmos|"
    r"true-?hd|lpcm|pcm|mp3|opus|ogg|vorbis|alac|wma|wav|aiff|ape|"
    r"mp4|mkv|avi|webm|m2ts|wmv|flv|"
    r"hdr10?(?:plus)?|hlg|sdr|hdr|dd|dovi|ma|"
    r"5\.1|7\.1|2\.0|\d+bit|multi|dual|nordic|"
    r"dsnp|dnsp|osn|web|hmax|hulu|atvp|peacock|para|itunes|"
    r"multiaudios?|arsub|multisubs?|"
    r"rerip|readnfo|internal|extended|unrated|"
    r"complete|retail|proper|repack|amzn|nf|"
    r"imax|sbs|interlaced|progressive|openmatte|anamorphic|"
    r"pal|ntsc)\b"
    r"|\b5\s*1\b|\b7\s*1\b"
    r"|\s*-\s*[A-Za-z0-9]+\s*$",            # trailing scene group: "-GRACE", "-OnlyWeb"
    re.IGNORECASE,
)


def _clean_search_title(raw: str) -> str:
    """Strip torrent release-group noise so TMDB search gets a clean movie name."""
    # First pass: strip common noise patterns.
    t = re.sub(r"\[[^\]]*\]", " ", raw)          # [1080p] [BluRay] [5.1]
    t = re.sub(r"[()]", " ", t)                    # (2026) parens
    t = re.sub(r"\b(?:19|20)\d{2}\b", " ", t)     # bare 2025, 2012
    t = SEARCH_NOISE_RE.sub(" ", t)                # quality tokens, codecs, etc.
    # Second pass: strip scene groups that are now at the end (after all
    # noise removal, "x264-hallowed" → "-hallowed" at the actual string end).
    t = re.sub(r"\s*-\s*[A-Za-z0-9]+\s*$", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Drop trailing single-character tokens that are quality residue (e.g. "5 1", "H 265")
    while True:
        m = re.search(r"\s+(\S)$", t)
        if m:
            t = t[:m.start()].rstrip()
        else:
            break
    return t.strip()


def _build_imdb_link(title: str, year: str | None) -> str:
    """Build an IMDb search link, matching the format used by site description selectors."""
    query = f"{title} ({year})" if year else title
    q = quote(query, safe="")
    return (
        f'<a href="https://www.imdb.com/find?q={q}&amp;s=tt" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">IMDb</b></a>'
    )


def _build_trailer_link(title: str, year: str | None) -> str:
    """Build a YouTube trailer search link, matching the format used by site description selectors."""
    query = f"{title} ({year})" if year else title
    q = quote(query, safe="")
    return (
        f'<a href="https://www.youtube.com/results?search_query='
        f'{q}+preview%7Cpromo%7Ctrailer+-fake+-fan&amp;sp=EgIYAQ%253D%253D" '
        f'target="_blank" rel="noopener noreferrer">'
        f'<b style="color:#6600cc;">Trailer</b></a>'
    )


def _title_matches(tmdb_title: str, feed_title: str) -> bool:
    """Check if TMDb title validates against feed title to avoid wrong-ID lookups."""
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


def process_feed(path: Path) -> tuple[bool, dict]:
    """Process a single feed file. Returns (changed, stats)."""
    stats: dict = {"items": 0, "posters": 0, "years": 0, "future": 0, "errors": 0, "skipped": 0, "links": 0}
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        stats["errors"] = 1
        return False, stats

    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        stats["errors"] = 1
        return False, stats

    changed = False

    # --- Removal pass: filter future-dated (unreleased) items ---
    # COMMENTED OUT: unreleased-movie filtering disabled per request.
    # for item in list(channel.findall("item")):
    #     link_el = item.find("link")
    #     if link_el is None or not link_el.text:
    #         continue
    #     info = _lookup_link(link_el.text)
    #     if info and info.release_date:
    #         try:
    #             release = date.fromisoformat(info.release_date)
    #             if release > date.today():
    #                 title_text = item.findtext("title", "")
    #                 channel.remove(item)
    #                 stats["future"] += 1
    #                 changed = True
    #         except (ValueError, TypeError):
    #             pass

    for item in channel.findall("item"):
        stats["items"] += 1
        link_el = item.find("link")
        if link_el is None or not link_el.text:
            stats["skipped"] += 1
            continue

        title_el = item.find("title")
        title_text = title_el.text.strip() if title_el is not None and title_el.text else ""
        has_year = bool(HAS_YEAR_RE.search(title_text))

        info = _lookup_link(link_el.text)
        if info is None:
            if title_text:
                search_title = _clean_search_title(title_text)
                info = search_movie(search_title)
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

        if info.year and not has_year and title_text:
            title_el.text = f"{title_text} ({info.year})"
            stats["years"] += 1
            changed = True

        # Skip poster replacement if img already from TMDB (site-native thumbnails still get replaced)
        desc_el = item.find("description")
        skip_poster = False
        if desc_el is not None and desc_el.text:
            existing_img = IMG_TAG_RE.search(desc_el.text)
            if existing_img and "image.tmdb.org" in existing_img.group(0):
                skip_poster = True

        # IMDb/trailer search links are generated when we touch the item's
        # description. Skip when the description already carries them so
        # re-runs stay idempotent.
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
                            # Insert links right after the (now-replaced) img tag.
                            if link_block:
                                after = IMG_TAG_RE.search(el.text)
                                if after:
                                    el.text = el.text[:after.end()] + link_block + el.text[after.end():]
                        else:
                            el.text = f'<img src="{info.poster_url}">' + link_block + "<br>" + el.text
                    else:
                        el.text = f'<img src="{info.poster_url}">' + link_block
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
    enriched = 0
    total_items = 0
    total_posters = 0
    total_years = 0
    total_future = 0
    total_errors = 0
    total_skipped = 0
    total_links = 0

    for idx, path in enumerate(xml_files, 1):
        feed_name = path.stem
        print(f"  [{idx}/{total_feeds}] {feed_name}... ", end="", flush=True)

        changed, stats = process_feed(path)

        total_items += stats["items"]
        total_posters += stats["posters"]
        total_years += stats["years"]
        total_future += stats["future"]
        total_errors += stats["errors"]
        total_skipped += stats["skipped"]
        total_links += stats.get("links", 0)

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
        if stats["skipped"]:
            parts += f" skipped={stats['skipped']}"
        if stats["future"]:
            parts += f" future={stats['future']}"
        if stats["errors"]:
            parts += f" ERRORS={stats['errors']}"
        print(f"[{status}] {parts}")

    summary = f"Enriched {enriched}/{total_feeds} feeds | {total_items} items"
    if total_posters:
        summary += f" | {total_posters} posters"
    if total_years:
        summary += f" | {total_years} years added"
    if total_skipped:
        summary += f" | {total_skipped} skipped"
    if total_links:
        summary += f" | {total_links} links added"
    if total_future:
        summary += f" | {total_future} future filtered"
    print(summary)


if __name__ == "__main__":
    main()
