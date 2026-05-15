#!/usr/bin/env python3
"""Post-process RSS feeds to fix common quality issues."""
from __future__ import annotations

import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs
import xml.etree.ElementTree as ET

FEEDS_DIR = Path(__file__).resolve().parent.parent / "feeds"

NEXT_IMAGE_RE = re.compile(r'/_next/image\?url=([^&"\' >]+)')
STREAM_PREFIX_RE = re.compile(r'^\s*Stream\s+', re.IGNORECASE)
YEAR_AT_END_RE = re.compile(r'\b(\d{4})\s*$')
YEAR_IN_URL_RE = re.compile(r'-(\d{4})-')

FIXED_IMG_SIZE = 'style="width:300px;height:auto;max-height:450px;object-fit:contain;display:block;border-radius:4px;" width="300"'

FIXES = {
    "next_image": True,
    "year_format": True,
    "stream_prefix": {"hydrahd-movies.xml"},
    "year_from_url": {"hydrahd-movies.xml"},
    "watch_links": {
        "1primeshows-movies.xml": {"type": "append", "suffix": "/watch"},
        "1primeshows-tv.xml": {"type": "append", "suffix": "/watch"},
        "bingebox-movies.xml": {"type": "replace", "old": "/movie/", "new": "/watch/movie/"},
        "bingebox-tv.xml": {"type": "replace", "old": "/show/", "new": "/watch/show/"},
        "hdtodayz-movies.xml": {"type": "replace", "old": "/movie/", "new": "/watch/movie/"},
        "hdtodayz-series.xml": {"type": "replace", "old": "/tv/", "new": "/watch/tv/"},
        "streamgoblin-movies.xml": {"type": "replace", "old": "/movie/", "new": "/player/movie/"},
        "streamgoblin-tv.xml": {"type": "replace", "old": "/tv/", "new": "/player/tv/"},
    },
}

def fix_next_image_url(url: str) -> str:
    m = NEXT_IMAGE_RE.search(url)
    if not m:
        return url
    encoded = m.group(1)
    decoded = unquote(encoded)
    decoded = unescape(decoded)
    return url.replace(m.group(0), decoded)

IMG_TAG_RE = re.compile(r'<img\s[^>]*>')
IMG_WIDTH_RE = re.compile(r'\s(width="[^"]*")')
POSTER_STYLE = 'style="width:300px;height:auto;max-height:450px;object-fit:contain;display:block;border-radius:4px;" width="300" loading="lazy"'

def fix_poster_style(desc: str) -> str:
    """Normalize all <img> tags to the same poster style."""
    def _replace(m):
        tag = m.group(0)
        # Remove any existing style attribute
        tag = re.sub(r'\sstyle="[^"]*"', '', tag)
        tag = re.sub(r'\s(width="[^"]*")', '', tag)
        tag = re.sub(r'\sloading="[^"]*"', '', tag)
        # Insert our standard style before the closing >
        if tag.endswith('/>'):
            tag = tag[:-2] + f' {POSTER_STYLE} />'
        else:
            tag = tag[:-1] + f' {POSTER_STYLE}>'
        return tag
    return IMG_TAG_RE.sub(_replace, desc)

def fix_description_html(desc: str) -> str:
    desc = fix_next_image_url(desc)
    desc = fix_poster_style(desc)
    return desc

def fix_title_year(title: str) -> str:
    m = YEAR_AT_END_RE.search(title)
    if m and f"({m.group(1)})" not in title:
        title = YEAR_AT_END_RE.sub(f"({m.group(1)})", title)
    return title

def add_year_from_url(title: str, link: str) -> str:
    if "(" in title and ")" in title:
        return title
    m = YEAR_IN_URL_RE.search(link)
    if m:
        year = m.group(1)
        title = f"{title} ({year})"
    return title

def _already_fixed(link: str, rule: dict) -> bool:
    if rule["type"] == "append":
        return link.endswith(rule["suffix"])
    if rule["type"] == "replace":
        return rule["new"] in link
    return False

DUP_WATCH_RE = re.compile(r'(/watch){2,}')

def _clean_dup_watch(link: str) -> str:
    return DUP_WATCH_RE.sub(r'/watch', link)

def _already_fixed(link: str, rule: dict) -> bool:
    if rule["type"] == "append":
        return link.endswith(rule["suffix"])
    if rule["type"] == "replace":
        return rule["new"] in link
    return False

def fix_link(link: str, feed_name: str) -> str:
    link = _clean_dup_watch(link)
    rule = FIXES["watch_links"].get(feed_name)
    if not rule or _already_fixed(link, rule):
        return link
    if rule["type"] == "append":
        link = link.rstrip("/") + rule["suffix"]
    elif rule["type"] == "replace":
        link = link.replace(rule["old"], rule["new"], 1)
    return link

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

    feed_name = path.name
    changed = False

    for item in channel.findall("item"):
        title_el = item.find("title")
        link_el = item.find("link")

        if title_el is not None and title_el.text:
            old = title_el.text
            t = old
            if feed_name in FIXES.get("stream_prefix", set()):
                t = STREAM_PREFIX_RE.sub("", t).strip()
            if feed_name in FIXES.get("year_from_url", set()):
                link = link_el.text if link_el is not None else ""
                t = add_year_from_url(t, link)
            else:
                t = fix_title_year(t)
            title_el.text = t
            if title_el.text != old:
                changed = True

        if link_el is not None and link_el.text:
            new_link = fix_link(link_el.text, feed_name)
            if new_link != link_el.text:
                link_el.text = new_link
                changed = True

        for tag in ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"]:
            el = item.find(tag)
            if el is not None and el.text:
                old = el.text
                el.text = fix_description_html(old)
                if el.text != old:
                    changed = True

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)
        print(f"  Fixed: {feed_name}")
        return True
    return False

def main():
    xml_files = sorted(FEEDS_DIR.glob("*.xml"))
    print(f"Processing {len(xml_files)} feeds in {FEEDS_DIR}...")
    fixed = 0
    for path in xml_files:
        if process_feed(path):
            fixed += 1
    print(f"Fixed {fixed}/{len(xml_files)} feeds.")

if __name__ == "__main__":
    main()
