#!/usr/bin/env python3
"""Enrich feed items with TMDb posters and years when IDs are found in links."""
from __future__ import annotations

import os
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from core.tmdb import movie_lookup, tv_lookup, find_by_imdb

FEEDS_DIR = Path(__file__).resolve().parent.parent / "feeds"

# Matches /movie/ID, /movie/slug/ID, /movie/ID-slug (and same for /tv/)
TMDB_ID_RE = re.compile(r"/(movie|tv)(?:/[^/]+)?/(\d{4,})(?:/|$|-)")
IMDB_ID_RE = re.compile(r"(tt\d{7,8})")
IMG_TAG_RE = re.compile(r'<img\s[^>]*>', re.IGNORECASE)
HAS_YEAR_RE = re.compile(r"\(\d{4}\)")


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


def process_feed(path: Path) -> bool:
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        print(f"  Parse error: {e}")
        return False

    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        return False

    changed = False

    for item in channel.findall("item"):
        link_el = item.find("link")
        if link_el is None or not link_el.text:
            continue

        info = _lookup_link(link_el.text)
        if info is None:
            continue

        title_el = item.find("title")
        title_text = title_el.text.strip() if title_el is not None and title_el.text else ""

        has_year = bool(HAS_YEAR_RE.search(title_text))

        # Skip API call only if both year and poster already present
        desc_el = item.find("description")
        if has_year and desc_el is not None and desc_el.text and IMG_TAG_RE.search(desc_el.text):
            continue

        if info.year and not has_year and title_text:
            title_el.text = f"{title_text} ({info.year})"
            changed = True

        if info.poster_url:
            new_poster = f'<img src="{info.poster_url}">'
            for tag in ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"]:
                el = item.find(tag)
                if el is not None and el.text:
                    # Replace any existing img tag, or prepend
                    if IMG_TAG_RE.search(el.text):
                        el.text = IMG_TAG_RE.sub(new_poster, el.text)
                    else:
                        el.text = new_poster + "<br>" + el.text
            changed = True

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)
        print(f"  Enriched: {path.name}")
        return True
    return False


def main():
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        print("TMDB_API_KEY not set — skipping enrichment")
        return

    xml_files = sorted(FEEDS_DIR.glob("*.xml"))
    print(f"Enriching {len(xml_files)} feeds with TMDb data in {FEEDS_DIR}...")
    enriched = 0
    for path in xml_files:
        if process_feed(path):
            enriched += 1
    print(f"Enriched {enriched}/{len(xml_files)} feeds.")


if __name__ == "__main__":
    main()
