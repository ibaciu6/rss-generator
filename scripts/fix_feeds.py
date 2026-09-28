#!/usr/bin/env python3
"""Post-process RSS feeds to fix common quality issues."""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import yaml

from core.config import load_config, resolve_feed_files
from core.feed import downscale_image_src, poster_style_for, poster_width_for_category

FEEDS_DIR = Path(__file__).resolve().parent.parent / "feeds"
SITES_CONFIG = Path(__file__).resolve().parent.parent / "config" / "sites.yaml"

NEXT_IMAGE_RE = re.compile(r'/_next/image\?url=([^&"\' >]+)')
STREAM_PREFIX_RE = re.compile(r'^\s*Stream\s+', re.IGNORECASE)
DUBLAT_IN_ROMANA_RE = re.compile(r'\s+dublat\s*în\s*română\s*$', re.IGNORECASE)

# Happy Cinema glues its presentation flags onto the movie title with underscores
# ("Răzbunătorii: Sfârșitul jocului_SUB_3D", "Resident Evil_UCRAINIANA
# /Обитель зла"). The information is not lost by removing it -- the description's
# "Formate:" field already carries the same data ("3d, SUB (ro)"), so the title
# was the redundant copy plus a stray original-language title.
#
# Anchored on a run of *known* flag tokens so it cannot bite a title that merely
# contains an underscore ("snake_case_identifier in a tech post"), and a trailing
# (YYYY) is captured and put back so fix_title_year still sees it.
FORMAT_FLAGS_RE = re.compile(
    r'[\s_]+(?:2D|3D|DUBLAT|DUB|SUB|UCRAINIANA)'
    r'(?:[\s_]+(?:2D|3D|DUBLAT|DUB|SUB|UCRAINIANA))*'
    r'(?:\s*/\s*.*?)?'
    r'(\s*\(\d{4}\))?\s*$',
    re.IGNORECASE,
)
YEAR_AT_END_RE = re.compile(r'\b(\d{4})\s*$')
YEAR_IN_URL_RE = re.compile(r'-(\d{4})-')

FIXES = {
    "next_image": True,
    "year_format": True,
    "stream_prefix": {"hydrahd-movies.xml"},
    "year_from_url": {"hydrahd-movies.xml"},
    "dublat_in_romana": {"deseneledublate-desene.xml"},
    "format_flags": {"happy-cinema-colosseum.xml", "happy-cinema-vitantis.xml"},
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


def _removals_by_feed_file() -> dict[str, tuple[str, ...]]:
    """Map feed_file -> the site's configured chrome-removal modules.

    Only sites that list ``removals:`` appear here, which in practice means the
    article-mode feeds. Streaming feeds have none and are never touched.
    """
    try:
        data = yaml.safe_load(SITES_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for site in (data.get("sites") or {}).values():
        feed_file = site.get("feed_file")
        mods = site.get("removals") or []
        if feed_file and mods:
            out[feed_file] = tuple(mods)
    return out


FEED_REMOVALS = _removals_by_feed_file()


def strip_configured_chrome(desc: str, removals: tuple[str, ...]) -> str:
    """Re-apply a site's removal modules to a description already in the feed.

    Enrichment applies the modules as part of fetching the article page, so an
    item only gets cleaned on a run where its page was reachable. This pass is
    the safety net for anything that reached the feed another way. It needs no
    network: the description is already HTML, so the same modules apply
    directly.

    The modules only ever delete, so running one over an already-clean
    description is a no-op.
    """
    from scripts.enrichers.removal_modules import apply_modules  # local: keeps core import-light

    try:
        return apply_modules(desc, list(removals))
    except Exception as e:  # never let a cosmetic pass break the build
        print(f"  chrome strip failed: {e}")
        return desc

# Feed-specific label cleanup. The selectors in config/sites.yaml no longer emit
# these fields; the strip here is a safety net that also cleans feeds generated
# before that change, so it can be dropped once no such feeds remain.
# (uflix-episodes was removed from the config: both uflix.cc and uflix.to
# return 522 from CI and there is no feed left for it to clean.)
STRIP_FIELD_SETS = {
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

# Categories whose images really are posters, where a fixed width is the point
# and the reader shows them as uniform cards. Article feeds are the other case:
# their images are illustrations, and clamping every one of them to the poster
# width left a 300px photo stranded in a 534px column with 234px of dead space
# beside it (measured on securelist, on every article illustration). The reader's
# own `max-width: 100%` already scales them sensibly.
_POSTER_CATEGORIES = frozenset(
    {"cinema", "episodes", "movies", "releases", "torrents"}
)


def _is_poster_feed(feed_name: str) -> bool:
    """Whether this feed's images should be pinned to the poster width.

    An unknown feed name keeps the old pinned behaviour rather than silently
    switching to article sizing: a site added today is more likely a poster feed
    than not, and a surprise in either direction is worse than the status quo.
    """
    if not feed_name or feed_name not in FEED_CATEGORIES:
        return True
    return (FEED_CATEGORIES.get(feed_name) or "").strip().lower() in _POSTER_CATEGORIES


def fix_poster_style(desc: str, feed_name: str = "") -> str:
    """Normalize <img> tags: poster sizing for poster feeds, plain scaling for articles.

    Every image still gets the src downscale, the border radius, lazy loading and
    `max-width: 100%` styling. The hard `width`/`width=` attribute is only forced
    on poster feeds; on an article feed it is left alone, because it was pinning
    illustrations to a size chosen for movie cards.
    """
    width = poster_width_for_category(FEED_CATEGORIES.get(feed_name, ""))
    if _is_poster_feed(feed_name):
        poster_style = f'style="{poster_style_for(width)}" width="{width}" loading="lazy"'
    else:
        poster_style = (
            'style="width:auto;height:auto;max-width:100%;max-height:450px;'
            'object-fit:contain;display:block;border-radius:4px;" loading="lazy"'
        )

    poster_feed = _is_poster_feed(feed_name)

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
        if poster_feed:
            # Only posters are re-pinned to a fixed width; an article feed keeps
            # whatever width the site chose, minus the attribute we just set.
            tag = IMG_WIDTH_RE.sub('', tag)
        tag = re.sub(r'\sloading="[^"]*"', '', tag)
        # Insert our standard style before the closing >.
        #
        # rstrip before appending: `tag[:-2]` removes "/>" but leaves the space
        # in front of it, and poster_style already starts with a space, so each
        # pass added one more and every <img> in the feed grew a byte per run.
        # fix_feeds re-runs on every deploy, so this compounded silently.
        tag = tag.rstrip()
        if tag.endswith("/>"):
            tag = tag[:-2].rstrip() + f" {poster_style} />"
        else:
            tag = tag[:-1].rstrip() + f" {poster_style}>"
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



# How many times the description stages may be re-run on one item before the
# result is written. Three is above the two the real feeds need; the cap exists
# so a stage that never settles fails loudly-ish instead of looping forever.
MAX_PASSES = 4


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
            if feed_name in FIXES.get("format_flags", set()):
                # Put a captured trailing (YYYY) back before continuing.
                m = FORMAT_FLAGS_RE.search(t)
                if m:
                    t = (t[: m.start()] + (m.group(1) or "")).strip()
            t = fix_title_year(t)
            link = link_el.text if link_el is not None else ""
            t = add_year_from_url(t, link)
            title_el.text = t
            current_title = title_el.text
            if title_el.text != old:
                changed = True

        removals = FEED_REMOVALS.get(feed_name)

        for tag in ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"]:
            el = item.find(tag)
            if el is not None and el.text:
                old = el.text
                text = old
                # Run the stages to a fixed point before writing. They are each
                # idempotent alone but not in sequence: the chrome pass removes
                # markup, which changes what the poster and link passes see next,
                # so a single pass left a feed that moved again on the following
                # run. Bounded because a genuine non-convergence must not hang
                # the build.
                for _ in range(MAX_PASSES):
                    nxt = fix_description_html(text, feed_name)
                    nxt = fix_search_links(nxt, current_title)
                    if removals:
                        nxt = strip_configured_chrome(nxt, removals)
                    if nxt == text:
                        break
                    text = nxt
                el.text = text
                if el.text != old:
                    changed = True

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)
        print(f"  Fixed: {feed_name}")
        return True
    return False

def main(argv: list[str] | None = None) -> int:
    """Post-process feeds, optionally restricted to specific sites.

    Args:
        argv: CLI arguments. ``--site NAME`` restricts the run to those feeds
            (repeatable, accepts a site name or a ``.xml`` filename). Omit it to
            process every feed in ``feeds/``.

    Returns:
        Process exit code: 0 on success, 1 if a requested site matched nothing.
    """
    parser = argparse.ArgumentParser(
        prog="fix_feeds",
        description="Post-process RSS feeds: year format, poster styling, link fixes.",
    )
    parser.add_argument(
        "--site",
        dest="sites",
        action="append",
        metavar="NAME",
        help=(
            "Only fix this feed (matches the site `name` or the feed_file, with or "
            "without .xml). Repeatable. Defaults to every feed in feeds/."
        ),
    )
    # argv or [] rather than argv: a programmatic main() call must never pick up
    # the *process* arguments, which is what argparse's None default would do.
    args = parser.parse_args(argv or [])

    # This script already loads sites.yaml for the per-category poster width, so
    # reuse that config to resolve --site instead of parsing the XML filenames.
    config = load_config(SITES_CONFIG)
    wanted, unmatched = resolve_feed_files(config, args.sites)
    for name in unmatched:
        print(f"ERROR unknown site: {name}", file=sys.stderr)
    if args.sites and not wanted:
        return 1

    xml_files = [p for p in sorted(FEEDS_DIR.glob("*.xml")) if not args.sites or p.name in wanted]
    if args.sites:
        missing = sorted(wanted - {p.name for p in xml_files})
        for feed_file in missing:
            print(f"ERROR no such feed file: {FEEDS_DIR / feed_file} (run generate for it first)", file=sys.stderr)
        if missing:
            return 1

    print(f"Processing {len(xml_files)} feeds in {FEEDS_DIR}...")
    fixed = 0
    for path in xml_files:
        if process_feed(path):
            fixed += 1
    print(f"Fixed {fixed}/{len(xml_files)} feeds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
