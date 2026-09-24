#!/usr/bin/env python3
"""Post-process RSS feeds to fix common quality issues."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import yaml

from core.feed import downscale_image_src, poster_style_for, poster_width_for_category

FEEDS_DIR = Path(__file__).resolve().parent.parent / "feeds"
SITES_CONFIG = Path(__file__).resolve().parent.parent / "config" / "sites.yaml"

NEXT_IMAGE_RE = re.compile(r'/_next/image\?url=([^&"\' >]+)')
STREAM_PREFIX_RE = re.compile(r'^\s*Stream\s+', re.IGNORECASE)
DUBLAT_IN_ROMANA_RE = re.compile(r'\s+dublat\s*în\s*română\s*$', re.IGNORECASE)
YEAR_AT_END_RE = re.compile(r'\b(\d{4})\s*$')
YEAR_IN_URL_RE = re.compile(r'-(\d{4})-')

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
IMG_SRC_RE = re.compile(r'\ssrc="[^"]*"')


def _category_by_feed_file() -> dict[str, str]:
    """Map feed_file -> category so re-normalization keeps per-category poster sizes."""
    try:
        data = yaml.safe_load(SITES_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        feed_file: (site.get("category") or "")
        for site in (data.get("sites") or {}).values()
        if (feed_file := site.get("feed_file"))
    }


FEED_CATEGORIES = _category_by_feed_file()

# Feed-specific label cleanup. The selectors in config/sites.yaml no longer emit
# these fields; the strip here is a safety net that also cleans feeds generated
# before that change, so it can be dropped once no such feeds remain.
STRIP_FIELD_SETS = {
    "uflix-episodes.xml": {"Genres", "IMDb"},
    "uindex-movies.xml": {"Uploaded", "Seeds", "Leechers"},
    "uindex-tv.xml": {"Uploaded", "Seeds", "Leechers"},
}

def strip_label_fields(desc: str, feed_name: str) -> str:
    """Drop the listed ``<strong>Label:</strong>`` fields (with any value) for a feed."""
    labels = STRIP_FIELD_SETS.get(feed_name)
    if not labels:
        return desc
    pattern = re.compile(
        r'<br/?>\s*<strong>(' + '|'.join(sorted(re.escape(label) for label in labels)) + r'):</strong>[^<]*(?=<br/?>|<a|$)'
    )
    return pattern.sub("", desc)

def fix_poster_style(desc: str, feed_name: str = "") -> str:
    """Normalize all <img> tags to the standard poster style for the feed's category."""
    width = poster_width_for_category(FEED_CATEGORIES.get(feed_name, ""))
    poster_style = (
        f'style="{poster_style_for(width)}" width="{width}" loading="lazy"'
    )

    def _replace(m):
        tag = m.group(0)
        # Downscale the source URL so readers fetch small poster bytes, not the
        # full-resolution originals the sources stamp into descriptions.
        src_m = IMG_SRC_RE.search(tag)
        if src_m:
            old_src = src_m.group(0)[6:-1]
            new_src = downscale_image_src(old_src, width)
            tag = tag.replace(src_m.group(0), f' src="{new_src}"')
        # Remove any existing style attribute
        tag = re.sub(r'\sstyle="[^"]*"', '', tag)
        tag = IMG_WIDTH_RE.sub('', tag)
        tag = re.sub(r'\sloading="[^"]*"', '', tag)
        # Insert our standard style before the closing >
        tag = tag[:-2] + f' {poster_style} />' if tag.endswith('/>') else tag[:-1] + f' {poster_style}>'
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

def fix_description_html(desc: str, feed_name: str) -> str:
    desc = fix_next_image_url(desc)
    desc = fix_poster_style(desc, feed_name)
    desc = strip_label_fields(desc, feed_name)
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
                el.text = fix_description_html(old, feed_name)
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
