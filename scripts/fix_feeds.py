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
DUBLAT_IN_ROMANA_RE = re.compile(r'\s+dublat\s*în\s*română\s*$', re.IGNORECASE)
YEAR_AT_END_RE = re.compile(r'\b(\d{4})\s*$')
YEAR_IN_URL_RE = re.compile(r'-(\d{4})-')

FIXED_IMG_SIZE = 'style="width:300px;height:auto;max-height:450px;object-fit:contain;display:block;border-radius:4px;" width="300"'

FIXES = {
    "next_image": True,
    "year_format": True,
    "stream_prefix": {"hydrahd-movies.xml"},
    "year_from_url": {"hydrahd-movies.xml"},
    "dublat_in_romana": {"deseneledublate-desene.xml"},
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

def fix_search_links(desc: str, title: str) -> str:
    """Add year (YYYY) to YouTube trailer and IMDb search links when title has it."""
    m = YEAR_IN_TITLE_RE.search(title)
    if not m:
        return desc
    year = m.group(1)
    year_enc = f"%20({year})"
    # Skip if year already present (bare or URL-encoded parentheses)
    if year_enc in desc or f"%20%28{year}%29" in desc:
        return desc
    desc = re.sub(
        r'(search_query=)([^+]+)(\+preview)',
        lambda mo: mo.group(1) + mo.group(2) + year_enc + mo.group(3),
        desc,
    )
    desc = re.sub(
        r'(q=)([^&]+)(&amp;?s=tt)',
        lambda mo: mo.group(1) + mo.group(2) + year_enc + mo.group(3),
        desc,
    )
    return desc

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
    for pat in [r'-(\d{4})-', r'-(\d{4})/', r'/(\d{4})/', r'-(\d{4})$', r'/(\d{4})$']:
        m = re.search(pat, link)
        if m:
            year = m.group(1)
            if 1900 <= int(year) <= 2099:
                return f"{title} ({year})"
    return title

YEAR_IN_TITLE_RE = re.compile(r'\((\d{4})\)\s*$')



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

        current_title = ""
        if title_el is not None and title_el.text:
            old = title_el.text
            t = old
            if feed_name in FIXES.get("stream_prefix", set()):
                t = STREAM_PREFIX_RE.sub("", t).strip()
            if feed_name in FIXES.get("dublat_in_romana", set()):
                t = DUBLAT_IN_ROMANA_RE.sub("", t).strip()
            t = fix_title_year(t)
            link = link_el.text if link_el is not None else ""
            t = add_year_from_url(t, link)
            title_el.text = t
            current_title = title_el.text
            if title_el.text != old:
                changed = True

        for tag in ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"]:
            el = item.find(tag)
            if el is not None and el.text:
                old = el.text
                el.text = fix_description_html(old)
                el.text = fix_search_links(el.text, current_title)
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
