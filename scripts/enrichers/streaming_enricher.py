#!/usr/bin/env python3
"""Streaming-site enrichment: TMDb posters/years, IMDb + trailer links, EpGuides.

Owned by ``scripts/enrich_feeds.py``, which routes each feed to a mode
(streaming / article / none) from its site category. This module is the
streaming mode; the per-feed entry point is :func:`process_feed`.
"""
from __future__ import annotations

import csv
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlsplit

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
# Every href/src in an HTML fragment, so "does this description already link
# to X" can be answered by whole hosts instead of substrings.
URL_ATTR_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# Torrent episode titles carry SxxEyy (e.g. "The Gentlemen 2024 S02E03 …") or NxM
# signal that a title is a TV series, so we look up TMDb /search/tv, not movies.
EPISODE_TITLE_RE = re.compile(r"\bS\d{1,2}\s*E\d{1,2}\b|\b\d+x\d+\b", re.IGNORECASE)
HAS_YEAR_RE = re.compile(r"\(\d{4}\)")
HAS_BARE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
YEAR_STRIP_RE = re.compile(r"[\(\[\{]\d{4}[\)\]\}]")
NON_WORD_RE = re.compile(r"[^\w\s]+")
# A final lowercase "h"/"x" is codec residue from "H 265"; a capital "X" is the
# last word of "American History X", so this is case-sensitive. Digits are
# deliberately NOT stripped: "Awarapan 2" and "Toy Story 4" are complete titles,
# and the "5 1" / "AAC2 0" residue is removed by the channel-count rules.
TRAILING_RESIDUE_RE = re.compile(r"\s+[hx]\s*$")
# The elements an item's HTML can live in, in visit order. `description` first
# because it is the field readers render; `content:encoded` is the alternative
# some sources use instead of having both.
DESC_TAGS = ("description", "{http://purl.org/rss/1.0/modules/content/}encoded")
# A trailing "-Group" is a scene release tag; a trailing hyphen in "X-Men" is
# part of the title. A release name is the one that also carries the digits of
# a year, resolution or codec, so gate the strip on a digit being present
# rather than on the hyphen's neighbour, which is a letter in both cases.
SCENE_GROUP_RE = re.compile(r"\s*-\s*[A-Za-z0-9]+\s*$")
RELEASE_MARKER_RE = re.compile(r"\d")

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
    r"ddp5?\.?1?|dd5|dts-?hd|dts|eac3|ac3|flac|aac|atmos|"
    r"true-?hd|lpcm|pcm|mp3|opus|ogg|vorbis|alac|wma|wav|aiff|ape|"
    r"dolby|digital|plus\d*|"
    r"mp4|mkv|avi|webm|m2ts|wmv|flv|"
    r"hdr10?(?:plus)?|hlg|sdr|hdr|dd|dv|dovi|ma|p7|"
    # Channel counts sit either side of the dot in a release name ("DDP5.1",
    # "DDP2.0") but get split by the dot-to-space pass below, which would
    # otherwise leave "DDP2 0" / "DDP5 1" behind.
    r"(?:5\.1|7\.1|2\.0)|\d+bit|multi|dual|nordic|"
    r"\d+\s*(?:gb|tb|mb|kb|hrs?|h|min|mins?)\b|"
    r"\d+\s*-\s*\d+\b|"
    # Vision-tag indices: "DV7", "HDR10+", "DV5". The digit is a profile number,
    # not part of any title, and it stranded "Toy Story 5 BDRemux DV7".
    r"\bdv\d+\b|"
    r"cinephiles|narchives|someone|btm|"
    # `it` is deliberately absent: it is a release-group tag only when it is a
    # prefix of a longer token ("iT.WEB-DL"), and dropping the bare word cost
    # the titles "It", "It Follows" and "Its Always Sunny in Philadelphia".
    r"dsnp|dnsp|osn|web|hmax|hulu|atvp|atv|peacock|para|itunes|amzn|nf|"
    r"multiaudios?|arsub|multisubs?|"
    # `internal`, `complete` and `extended` were listed as release tags, but
    # they are ordinary title words ("Internal Affairs", "A Complete Unknown")
    # and the release sense is already covered by proper/repack/rerip plus the
    # bracket pass. Left out rather than risk the truncation.
    r"rerip|readnfo|retail|proper|repack|"
    r"imax|sbs|interlaced|progressive|openmatte|anamorphic|hybrid|"
    r"bdremux|remux|pal|ntsc|lbxd|uhd|blu-?ray|blu|ray|"
    r"telesync|telecine|cam|hc|werk|dvd|remaster|remuxed|"
    # The closing \b on this group is load-bearing, not decoration. The group
    # opened with \b, so without a matching one every alternative matched as a
    # *prefix* of a longer word: "blu" ate "Blue", "web" ate "Webb", "dd" ate
    # "Daddy", "ts" ate "Tshirt". Each truncation then queried TMDb with a
    # wrong title, so the film/series was missed or matched to the wrong poster.
    r"doxxi|nosecret|layer|dual|disc)\b"
    r"|\b5\s*1\b|\b7\s*1\b"
    # Subtitle and source tags. Whole tokens only: bare "sub" and the language
    # names ("hindi", "english") are ordinary title words, and the network
    # shorthands are too short to strip safely, so only the unambiguous
    # multi-character forms are listed.
    r"|\b(?:esub|msub|bsub|hardsub|softsub|yts|yify|rarbg|eztv|subsplease|"
    r"tgx|paradox|remuxdoc|okko|gaste|rawhd)\b"
    # Language tags, only when they follow the title rather than sit inside
    # it: "Awarapan 2 … Hindi ESub" is a release, "Hindi" alone is a word.
    r"|(?:^|[.\s_])(?:hindi|tamil|telugu|kannada|bengali|malayalam|punjabi|"
    r"gujarati|urdu|korean|japanese|chinese|french|german|spanish|italian|"
    r"russian|dutch|arabic|hebrew|greek|turkish)(?=[.\s_]|$)",
    re.IGNORECASE,
)


def _strip_release_noise(raw: str, *, strip_bare_year: bool = True) -> str:
    """One stripping pass over a release name. See `_clean_search_title`."""
    # A release name is distinguishable from a plain title by the digits it
    # carries (year, resolution, codec). Several tags are only unambiguous in
    # that context, so resolve them here while the digits are still present:
    # a plain title has none, and its hyphen ("X-Men"), its "It" and its
    # "iT"-looking word are left alone. Deciding later is not an option --
    # the noise pass strips the digits that carry the signal.
    if RELEASE_MARKER_RE.search(raw):
        raw = SCENE_GROUP_RE.sub(" ", raw)
        # Same reasoning for the tags that double as title words. Inside a
        # release name they are tags ("Toy Story 5 2026 COMPLETE UHD BLURAY-…");
        # in a title they are words ("A Complete Unknown"), and only the digit
        # in the release name tells the two apart.
        raw = re.sub(r"(?:^|[.\s_])(?:complete|retail|final|real)(?=[.\s_]|$)", " ", raw, flags=re.IGNORECASE)
        # iT / IT = iTunes release tag. Only a token of its own: with the
        # boundary in place this leaves a title word such as "It" or "It Follows"
        # alone, which dropping the tag unconditionally did not.
        raw = re.sub(r"(?:^|[.\s_])iT(?=[.\s_]|$)", " ", raw, flags=re.IGNORECASE)
        # Undated titles keep their season/episode marker here ("MobLand
        # S02E02 …"), which TMDb indexes as a series, not an episode.
        raw = EPISODE_TITLE_RE.sub(" ", raw)
        # Bare runtime/episode-numbering noise: "AAC2 0" from "AAC2.0".
        raw = re.sub(r"\b(?:aac|ac3|eac3|ddp?)\s*\d\s*\.?\s*\d\b", " ", raw, flags=re.IGNORECASE)
    # First pass: strip common noise patterns.
    t = re.sub(r"\[[^\]]*\]", " ", raw)          # [1080p] [BluRay] [5.1]
    t = re.sub(r"[()]", " ", t)                    # (2026) parens
    # Scene-style names separate tokens with dots/underscores (e.g.
    # "I.Want.Your.Sex.2026.2160p.AMZN.WEB-DL.DDP5.1-H.265-SCOPE"); normalize
    # to bare words so the noise regex can match `\b2026\b`, `\bDV\b`, etc.
    # Bracketed channel counts ("[5.1]") go first: splitting "DDP5.1" here
    # would strand "DDP5 1", which no later pass removes.
    t = re.sub(r"\b(?:ddp?|dd|eac3|ac3)\s*[0-9]\s*[._]\s*[0-9]\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"[._]+", " ", t)
    if strip_bare_year:
        t = re.sub(BARE_YEAR_RE, " ", t)           # bare 2025, 2012
    t = SEARCH_NOISE_RE.sub(" ", t)                # quality tokens, codecs, etc.
    # Tilde markers (e.g. "16bit~COD3D") are leftover scene-group residues.
    t = re.sub(r"\s*~+\s*[A-Za-z0-9]*\s*$", " ", t)
    # Dangling separators left by the passes above: "HDR10+H.265-XEBEC" ends
    # with a bare "+" once both its neighbours are gone. Collapse runs of
    # punctuation/whitespace to single spaces rather than deleting outright, so
    # the hyphen inside "X-Men" survives as the word break it is.
    t = re.sub(r"[-\s]+", " ", t)
    t = re.sub(r"\s*[+&]\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -+&")
    # Drop a trailing lowercase codec letter left by the passes above ("H 265"
    # → "H"). Case-sensitive, and no longer a blanket single-character strip:
    # that turned "American History X" into "American History" and "M" into
    # nothing, while a trailing digit is a sequel number ("Awarapan 2").
    while True:
        m = TRAILING_RESIDUE_RE.search(t)
        if m:
            t = t[: m.start()].rstrip()
        else:
            break
    return t.strip()


_PONTV_TITLE_RE = re.compile(r"^\d+\.\d+(.+?)\d{4}\s*[•·]")

# uIndex titles: "Movie Title Year QUALITY TAGS Group"
# The year is the release year and should be preserved for searching.
# Pattern: Title (words) + Year (4 digits) + quality tags
_UINDEX_TITLE_RE = re.compile(
    r"^(.+?)\s+(19\d\d|20[0-2]\d)\s+"
    r"(?:REPACK|PROPER|REPACK|1080p|720p|2160p|4K|UHD|BLURAY|WEBRip|WEB-DL|x264|x265|HEVC|H\.?264|H\.?265|DDP|DTS|Atmos|5\.1|7\.1)"
)

def _clean_pontv_title(raw: str) -> str:
    """Clean pontv-style titles like '7.2Swapped2026 • Adventure • Animation'.

    Extracts the title portion between the rating prefix and year/genres.
    """
    m = _PONTV_TITLE_RE.search(raw)
    if m:
        # Return just the title part, stripped
        return m.group(1).strip()
    return raw


def _clean_uindex_title(raw: str) -> str:
    """Clean uIndex-style titles like 'Spider-Man Brand New Day 2026 REPACK 1080p 10bit WEBRip 6CH x265 HEVC-PSA'.

    Extracts the title and year, preserving the year for TMDb search.
    Returns a tuple of (cleaned_title, year, hyphen_positions) if matched, else (raw, None, None).
    """
    m = _UINDEX_TITLE_RE.search(raw)
    if m:
        title = m.group(1).strip()
        year = m.group(2)
        # Replace ASCII hyphens with a placeholder that won't be affected by noise stripping
        # Use a unique string without hyphens, underscores, dots, or noise patterns
        title = title.replace('-', 'HYPHENPLACEHOLDER12345')
        return f"{title} ({year})", year, True
    return raw, None, False


def _clean_search_title(raw: str) -> str:
    """Strip torrent release-group noise so TMDB search gets a clean movie name.

    When the year strip empties the title, retry keeping it. A film whose title
    *is* a year -- "1917", "1984" -- is otherwise stripped down to nothing and
    searched for as an empty string, which matches nothing and loses the poster
    for a title TMDb does have. The retry costs one regex pass and only runs
    for a title that was already going to fail.
    """
    # Pre-clean pontv-style titles
    raw = _clean_pontv_title(raw)
    # Pre-clean uindex-style titles (returns tuple of (cleaned_title, year, has_hyphens))
    raw, uindex_year, has_hyphens = _clean_uindex_title(raw)
    cleaned = _strip_release_noise(raw)
    if cleaned or not BARE_YEAR_RE.search(raw):
        # If uindex year was stripped, restore it
        if uindex_year and uindex_year not in cleaned:
            # Check if year was in parentheses and got stripped
            if f"({uindex_year})" in raw or f"({uindex_year})" in cleaned:
                # Year was in parentheses and got stripped, restore it
                cleaned = cleaned.rstrip() + f" ({uindex_year})"
        # Restore hyphens in uindex titles
        if has_hyphens:
            # Replace placeholder with hyphen
            cleaned = cleaned.replace('HYPHENPLACEHOLDER12345', '-')
        return cleaned
    kept = _strip_release_noise(raw, strip_bare_year=False)
    tokens = kept.split()
    # Every token left is a year, so the title is a year: "1917.2019.1080p" is
    # the film 1917, released in 2019. Scene naming puts the title first, so
    # search the first year rather than the pair -- "1917 2019" is a query for
    # nothing, "1917" is the film. A survivor that is not a year means the
    # title has a word in it after all, and that whole string is the query.
    if tokens and all(BARE_YEAR_RE.fullmatch(tok) for tok in tokens):
        return tokens[0]
    return kept


def _cut_at_episode_marker(title: str) -> str:
    """Return the part of a release title before its SxxEyy / NxM marker.

    Everything from the marker on is the episode name plus quality flags, which
    TMDb does not index, so the head is the only part worth searching.
    """
    m = EPISODE_TITLE_RE.search(title)
    return title[: m.start()] if m else title


def _series_search_title(raw: str) -> str:
    """Best-effort series name for a TV release title, for TMDb /search/tv.

    A scene name hangs the episode title *and* the quality flags off the
    marker -- "Saturday Night Live S52E01 Jalen Brunson 1080p WEB h264-GRACE" --
    so stripping noise across the whole string leaves the residue
    "Saturday Night Live Jalen Brunson", which TMDb has no record of and which
    every such lookup misses on. Cut at the marker first, then strip what is
    left: the series name plus the odd network token ("Taskmaster NZ S07E05
    Rhys Lightning 1080p TVNZ WEB-DL ..." -> "Taskmaster NZ").

    Marker-first titles ("S01E01 The Show") leave no head to cut, so fall back
    to stripping the whole string.
    """
    head = _clean_search_title(_cut_at_episode_marker(raw))
    if head:
        return head
    # Nothing precedes the marker (a title that *starts* with one), so the
    # series name sits after it. Drop the marker and strip the residue, and
    # keep the first run of words that looks like a name: the episode title
    # trails the series name, with no delimiter to tell them apart.
    rest = re.sub(EPISODE_TITLE_RE, " ", raw, count=1)
    cleaned = _clean_search_title(rest)
    return " ".join(cleaned.split()[:3]) if cleaned else ""


# A request is conversational prose, and the reliable signal is the *verb* --
# "has anyone seen", "looking for", "does anyone know" -- not a bare noun,
# which is why "anyone" and "please" on their own are not enough. These are all
# two-word or hyphenated forms, so none of them can match inside a title.
REQUEST_VERB_RE = re.compile(
    r"\b(?:"
    r"(?:has|have|had)\s+anyone|does\s+anyone|did\s+anyone|anyone\s+(?:seen|else|know|got)|"
    r"anybody\s+(?:seen|got|know)|who\s+(?:has|knows|else|got|can)|"
    r"(?:i'?m|i\s+am|we'?re|we\s+are)\s+(?:looking|waiting|searching|hunting)|"
    r"looking\s+for|searching\s+for|can\s+(?:someone|anybody|anyone)|"
    r"where\s+(?:can|do|is|are)|how\s+(?:can|do|to)|"
    r"not\s+available|anyone\s+seen|please\s+(?:help|let|adv|can)|"
    r"need\s+(?:this|help|someone)|help\s+me|"
    r"i\s+(?:want|need|search|have\s+seen)|i'?ll\s+watch|i'?d\s+watch|"
    r"wanna(?:\s+\w+)?|removed\s+that|my\s+local|"
    r"still\s+(?:not|no\s+one|searching)|can'?t\s+find|couldn'?t\s+find|"
    r"(?:in|on)\s+(?:my|our)\s+(?:local|country|region)|"
    r"released\s+weeks?\s+ago|any\s+(?:quality|region|place|site)"
    r")\b",
    re.IGNORECASE,
)
# Sentence punctuation and a conversational opener, used only to confirm a verb
# hit so a punctuated title with no request language is not caught.
REQUEST_OPENER_RE = re.compile(
    r"[?!]|\b(?:hello|hi|hey|guys|fellows|people|folks|someone|somebody)\b",
    re.IGNORECASE,
)
# A scene name is written without spaces, tokens glued by dots or underscores
# ("Nimrods.2025.2160p.AMZN.WEB-DL.DDP5.1.H.265-BYNDR"), and a request post is
# ordinary sentences. Punctuation count is the discriminator, and it is the
# right one: a request can still *mention* a resolution ("… 1080p would be
# fine!"), which a keyword test would have to treat as a release.
SCENE_FORM_RE = re.compile(r"[._]{2,}|\.[A-Za-z0-9]*\d|[A-Z0-9]{2,}-[A-Za-z0-9]{2,}\s*$")

# A bare 4-digit year in a release name, bounded so a number that is part of a
# title survives: "Blade Runner 2049" and "1917" keep their digits. A year
# outside 1900-2029 is not a release year for anything this feed can carry, so
# leaving it in place only ever adds noise. The one title that is itself a bare
# year ("1984") is still stripped, as it was before this bound existed.
BARE_YEAR_RE = re.compile(r"\b(?:19\d\d|20[0-2]\d)\b")


def is_request_post(title: str) -> bool:
    """True when a post title reads as a request, not a release announcement.

    ``r/SceneReleases`` mixes release names ("Nimrods.2025.2160p.AMZN.WEB-DL…")
    with posts asking whether a title exists. The requests are not films, so no
    TMDb search can match them and the lookup is wasted either way.

    Both signals are needed: a request verb, and either a sentence mark or a
    conversational opener. Release structure vetoes the result, so a real title
    that happens to read conversationally ("Please Please Me (1981) 1080p
    BluRay x264-GRP") keeps its poster. Erring toward keeping the item is
    deliberate -- a missed poster costs one image, a false skip costs a lookup
    that was about to fail anyway.
    """
    if not title or not title.strip():
        return False
    if SCENE_FORM_RE.search(title):
        return False
    return bool(REQUEST_VERB_RE.search(title) and REQUEST_OPENER_RE.search(title))


def _normalize_epguides_title(title: str) -> str:
    """Fold punctuation/spaces/case so scene names match EpGuides titles."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _epguides_series_title(title: str) -> str:
    """Extract the series name from an episode title.

    Unlike ``_clean_search_title`` (tuned for movie release names), this keeps
    everything before the SxxEyy / NxM marker, so formats like
    ``KAOS - S1 E2 - Episode 2`` and ``Women in Blue - 2x5`` yield the series
    name instead of trailing episode/quality residue.
    """
    t = YEAR_STRIP_RE.sub(" ", title)
    t = _cut_at_episode_marker(t)
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


def _refresh_epguides_mapping() -> dict[str, str] | None:
    """Re-download allshows.txt; refresh the local mirror only when it changed.

    Returns the fresh title→slug map when the online list differs from the
    local copy, else None. Skips network when ``EPGUIDES_DISABLE_DOWNLOAD`` set.
    """
    cache = EPGUIDES_CACHE
    old = cache.read_bytes() if cache.exists() else b""
    if os.environ.get("EPGUIDES_DISABLE_DOWNLOAD"):
        return None
    try:
        resp = httpx.get(EPGUIDES_URL, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None
    if resp.content != old:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(resp.content)
        return _load_epguides(cache)
    return None


def _find_epguides_slug(series_title: str, mapping: dict[str, str]) -> str | None:
    """Look up an EpGuides page slug for a series name (scene formatting tolerated)."""
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


# The IMDb anchor. EpGuides is inserted directly after it, so the three
# generated links read as one block above the article: Trailer, IMDb, EpGuides,
# then the body. Appending at the end instead put it after the prose, which
# read as a stray link dropped at the bottom of the item.
#
# Only the anchor is matched, not the <br> that follows: the link being
# inserted carries its own leading <br>, and consuming the existing one as well
# produced a doubled break.
#
# The path is written as `find/?` because that is the spelling the Romanian
# cinema sites use in their own description selectors, and on those items no
# link of ours is added (see _has_imdb_search_link), so the anchor being matched
# is theirs. Matching only our own `/find?` meant the EpGuides link was appended
# after the prose on every cinema item instead of sitting with the other two.
_IMDB_ANCHOR_END = re.compile(
    r'<a href="https://www\.imdb\.com/find/?\?[^"]*"[^>]*>\s*<b[^>]*>IMDb</b>\s*</a>',
    re.IGNORECASE,
)


def _insert_after_imdb(text: str, link: str) -> str:
    """Put `link` immediately after the IMDb anchor, else append it.

    `link` carries its own leading ``<br>``, so the existing separator after
    IMDb is left in place and the block stays uniformly ``<br>``-separated.
    """
    m = _IMDB_ANCHOR_END.search(text or "")
    if not m:
        return (text or "") + link
    return (text or "")[: m.end()] + link + (text or "")[m.end() :]


def _attach_epguides_link(
    item: ET.Element,
    series_title: str,
    mapping: dict[str, str],
    *,
    allow_fallback: bool = False,
) -> bool:
    """Add the EpGuides link to an item's description/encoded.

    Uses the exact EpGuides page when the series is known; with
    ``allow_fallback`` an EpGuides site-search link is used otherwise, so every
    TV item ends up linked. The link is placed after the IMDb anchor so the
    generated links sit together above the article; with no IMDb link to sit
    after, it falls back to appending. Idempotent -- returns True when the link
    was added.
    """
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
        target.text = _insert_after_imdb(target.text or "", eg_link)
        added = True
    encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if encoded is not None and "EpGuides</b>" not in (encoded.text or ""):
        encoded.text = _insert_after_imdb(encoded.text or "", eg_link)
        added = True
    return added


def _feed_kinds() -> dict[str, str]:
    """Map feed filename → site config ``kind`` ("movie"/"series") when declared."""
    try:
        from core.config import load_config  # local import: module may run standalone
    except ImportError:
        return {}

    config_path = REPO_ROOT / "config" / "sites.yaml"
    if not config_path.exists():
        return {}

    config = load_config(config_path)
    return {site.feed_file: site.kind for site in config.sites if site.kind}


def _feed_categories() -> dict[str, str]:
    """Map feed filename → site config ``category`` when declared."""
    try:
        from core.config import load_config  # local import: module may run standalone
    except ImportError:
        return {}

    config_path = REPO_ROOT / "config" / "sites.yaml"
    if not config_path.exists():
        return {}

    config = load_config(config_path)
    return {site.feed_file: site.category for site in config.sites if site.category}


def _host_of(url: str) -> str:
    """The bare host of a URL, lowercased, with any userinfo and port removed."""
    try:
        netloc = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    return netloc.rsplit("@", 1)[-1].split(":", 1)[0]


def _host_is(host: str, want: str) -> bool:
    """Whether `host` is `want` or one of its subdomains."""
    return host == want or host.endswith("." + want)


def _links(blob: str) -> list[tuple[str, str]]:
    """(host, path) for every href/src in an HTML fragment.

    The guards below ask "has this item already been enriched?" by looking for
    a TMDb poster and an IMDb link in the description written last run. Testing
    that with `"image.tmdb.org" in blob` cannot tell a poster from
    `https://tracker.example/?ref=image.tmdb.org`, and a false match is not
    cosmetic -- it skips the enrichment the item still needs, silently and
    permanently. Comparing parsed hosts is both the correct test and the cheap
    one.
    """
    out: list[tuple[str, str]] = []
    for raw in URL_ATTR_RE.findall(blob or ""):
        try:
            parts = urlsplit(raw)
        except ValueError:
            continue
        out.append((_host_of(raw), parts.path))
    return out


def _has_host(blob: str, want: str) -> bool:
    """Whether the fragment links to `want` (or a subdomain of it)."""
    return any(_host_is(host, want) for host, _ in _links(blob))


def _has_path(blob: str, want: str, path_want: str) -> bool:
    """Whether the fragment links to `want` at exactly `path_want`.

    Exact, not a prefix. The question is "did *we* already write this link?",
    and the sites ship their own IMDb search links -- CinemaCity uses
    ``imdb.com/find/?q=...&ttype=ft``, with a trailing slash and a parameter
    this module never emits. Matching those as ours suppressed the trailer and
    IMDb links on 100 items across 9 cinema feeds, permanently.
    """
    return any(_host_is(host, want) and path == path_want for host, path in _links(blob))


# "Is this link already on the page?" is a different question from "did we put it
# there?", and mixing the two is what produced two of each on 57 cinema items.
# The Romanian cinema sites write their own Trailer and IMDb anchors in the
# XPath `description_selector`; this module then wrote a second pair in front of
# them, because its guard recognised only the exact spelling it emits
# (`/find?`, no slash) and the sites' `/find/?q=...&ttype=ft` did not match.
# Guarding per link rather than per pair is what closes that gap without
# reopening the other one: a site that ships an IMDb search and no trailer link
# still gets the trailer, because the two decisions are separate.
_TRAILER_LABEL_RE = re.compile(r">\s*Trailer\s*<", re.IGNORECASE)
_IMDB_LABEL_RE = re.compile(r">\s*IMDb\s*<", re.IGNORECASE)


def _has_trailer_link(blob: str) -> bool:
    """Whether the description already shows a Trailer link.

    Matched on the link's *label*, not its URL: the sites build their own
    YouTube search query and so does this module, and the two spellings differ
    in the order of the `preview|promo|trailer` terms. The label is what the
    reader sees, and it is what makes the second copy a duplicate.
    """
    return bool(_TRAILER_LABEL_RE.search(blob or ""))


def _has_imdb_search_link(blob: str) -> bool:
    """Whether the description already shows an IMDb search link.

    Matched on the label *and* the ``/find`` path, so a direct title link --
    ``imdb.com/title/tt1234567/`` -- does not stand in for a search link. Those
    point at one film and cannot stand in for "find this film".
    """
    if not _IMDB_LABEL_RE.search(blob or ""):
        return False
    return any(
        _host_is(host, "imdb.com") and path.rstrip("/") == "/find"
        for host, path in _links(blob)
    )


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


def _build_cinesrc_link(tmdb_id: int | None, media_type: str | None, title: str = "") -> str | None:
    """Build a CineSrc embed link when TMDB ID is available."""
    if not tmdb_id:
        return None
    if media_type == "tv":
        # Try to extract season/episode from title
        m = re.search(r'S(\d{1,2})E(\d{1,2})', title, re.IGNORECASE)
        if m:
            s, e = m.group(1), m.group(2)
            return (
                f'<a href="https://cinesrc.st/embed/tv/{tmdb_id}?s={int(s)}&e={int(e)}" target="_blank" '
                f'rel="noopener noreferrer"><b style="color:#6600cc;">CineSrc</b></a>'
            )
        return (
            f'<a href="https://cinesrc.st/embed/tv/{tmdb_id}" target="_blank" '
            f'rel="noopener noreferrer"><b style="color:#6600cc;">CineSrc</b></a>'
        )
    return (
        f'<a href="https://cinesrc.st/embed/movie/{tmdb_id}" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">CineSrc</b></a>'
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


def process_feed(
    path: Path,
    epguides_mapping: dict[str, str] | None = None,
    epguides_misses: dict | None = None,
    is_series_feed: bool | None = None,
    feed_category: str | None = None,
) -> tuple[bool, dict]:
    """Process a single feed file. Returns (changed, stats).

    ``epguides_mapping`` is the normalized-title→slug map; when omitted it is
    loaded (and downloaded when stale) through ``_epguides_map()``. When a TV
    series in the feed is missing from the map, ``epguides_misses`` (if given)
    records its feed path so the caller can retry after an online refresh.

    ``is_series_feed`` comes from the site config's ``kind`` field: True for a
    series feed, False for a movie feed. When None, a per-item title heuristic
    (SxxEyy / NxM marker) decides whether EpGuides links apply.
    """
    stats: dict = {"items": 0, "posters": 0, "years": 0, "future": 0, "errors": 0, "skipped": 0, "links": 0, "epguides": 0}
    # Resolve the map when the caller omits it. Without this, the `if ... and
    # epguides_mapping` guard below is False and EpGuides linking is silently
    # skipped for every item - the documented default has to actually load it.
    if epguides_mapping is None:
        epguides_mapping = _epguides_map()
    # Determine if this is a torrent/release feed that should get CineSrc links
    is_torrent_feed = False
    if feed_category:
        is_torrent_feed = feed_category in ("torrents", "releases")
    else:
        # Fallback: check common names
        fname = path.name.lower()
        if "torrent" in fname or "scene" in fname or "release" in fname or "uindex" in fname:
            is_torrent_feed = True
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
        has_bare_year = bool(HAS_BARE_YEAR_RE.search(title_text))

        # TV episode titles link to the series' EpGuides page. The feed's
        # declared `kind` (when set) decides series-ness; otherwise fall back to
        # the SxxEyy / NxM marker heuristic. Resolved independently of the TMDb
        # match below so series that TMDb has no poster for still get the link.
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

        # Already-enriched items (poster + IMDb link present) need no further
        # TMDb lookups. Skip the API round-trip; epguides was handled above.
        existing_enriched = item.findtext("description", "") or ""
        if _has_host(existing_enriched, "image.tmdb.org") and _has_path(
            existing_enriched, "imdb.com", "/find"
        ):
            continue

        info = _lookup_link(link_el.text)
        if info is None:
            # A request post names no film, so there is nothing for TMDb to
            # match. Checked before the search, not after a miss, so these
            # never spend an API call or a cache slot.
            if title_text and is_request_post(title_text):
                stats["skipped"] += 1
                continue
            if title_text:
                # TV: cut at the episode marker before noise-stripping, so the
                # query is the series name and not "Series Name Ep Title 1080p".
                # Movies keep the whole-title strip.
                search_title = (
                    _series_search_title(title_text)
                    if is_tv
                    else _clean_search_title(title_text)
                )
                # Extract year from URL if present (e.g. "the-box-2026" → "2026")
                year_from_url = None
                url_year_match = URL_YEAR_RE.search(link_el.text)
                if url_year_match:
                    year_from_url = url_year_match.group(1)
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

        # Replace messy title with clean TMDb title when available
        if info.title and title_text and not _title_matches(info.title, title_text) is False:
            # Use TMDb's canonical title
            title_el.text = info.title
            changed = True

        if info.year and not has_year and not has_bare_year and title_text:
            # Only add year if title doesn't already have it (after potential replacement)
            if not HAS_YEAR_RE.search(title_el.text or ""):
                title_el.text = f"{title_el.text} ({info.year})"
                stats["years"] += 1
                changed = True

        # Skip poster replacement if img already from TMDB (site-native thumbnails still get replaced)
        desc_el = item.find("description")
        skip_poster = False
        if desc_el is not None and desc_el.text:
            existing_img = IMG_TAG_RE.search(desc_el.text)
            if existing_img and _has_host(existing_img.group(0), "image.tmdb.org"):
                skip_poster = True

        # IMDb/trailer search links are generated when we touch the item's
        # description. Each is decided on its own, so re-runs stay idempotent
        # and a site that already links one of them does not end up with two.
        existing_desc = desc_el.text if desc_el is not None else ""
        want_trailer = not _has_trailer_link(existing_desc or "")
        want_imdb = not _has_imdb_search_link(existing_desc or "")
        want_cinesrc = "CineSrc</b>" not in (existing_desc or "")

        # Build link block (trailer, IMDb, CineSrc) - always generate if needed
        link_title = info.title or title_text
        link_block = ""
        parts = []
        if link_title and want_trailer:
            link_title_clean = YEAR_STRIP_RE.sub("", link_title).strip()
            parts.append(_build_trailer_link(link_title_clean, info.year))
        if link_title and want_imdb:
            link_title_clean = YEAR_STRIP_RE.sub("", link_title).strip()
            parts.append(_build_imdb_link(link_title_clean, info.year))
        # For torrent feeds, CineSrc goes at the END of description, not after poster
        cinesrc_link = None
        if is_torrent_feed:
            cinesrc_link = _build_cinesrc_link(getattr(info, 'tmdb_id', None), getattr(info, 'media_type', None), title_text)
            if cinesrc_link and want_cinesrc:
                # Don't add to parts - will append at end of description
                pass
        if parts:
            link_block = "<br>" + "<br>".join(parts)
        # Resolve the elements that hold this item's HTML *before* writing, and fall
        # back only when the item has neither. A per-tag fallback cannot
        # work here: it names a `<description>`, so on the second iteration
        # (`content:encoded`, still missing) it created a *second*
        # `<description>` instead of the tag being visited. That is how every
        # r/SceneReleases item ended up with two, and `fix_feeds.py` reaches
        # only the first with `item.find()`, leaving the duplicate with a raw
        # full-resolution poster -- so readers disagreed about which
        # description to render and the same item showed a 300px poster in
        # one and a 500px one in another.
        targets = [
            el for el in (item.find(tag) for tag in DESC_TAGS) if el is not None
        ]
        if not targets:
            # Feed items without a description element get one created
            # (e.g. native RSS/Atom feeds like Reddit).
            created = ET.SubElement(item, "description")
            created.text = ""
            targets = [created]

        # Poster replacement (if poster available and not skipped)
        if info.poster_url and not skip_poster:
            for el in targets:
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
        else:
            # No poster replacement, but still insert links if needed
            if link_block:
                for el in targets:
                    if el.text:
                        # Insert links at the beginning of the description
                        el.text = link_block + "<br>" + el.text
                    else:
                        el.text = link_block
                stats["links"] += 1
                changed = True
            elif getattr(info, 'tmdb_id', None) and is_torrent_feed:
                # Have TMDb ID even without poster replacement - add CineSrc for torrent feeds
                want_cinesrc = "CineSrc</b>" not in (existing_desc or "")
                cinesrc_link = _build_cinesrc_link(getattr(info, 'tmdb_id', None), getattr(info, 'media_type', None), title_text)
                if cinesrc_link and want_cinesrc:
                    targets = [el for el in (item.find(tag) for tag in DESC_TAGS) if el is not None]
                    if not targets:
                        created = ET.SubElement(item, "description")
                        created.text = ""
                        targets = [created]
                    for el in targets:
                        el.text = (el.text or "") + "<br>" + cinesrc_link
                    stats["links"] += 1
                    changed = True

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return changed, stats


def resolve_epguides_misses(
    epguides_misses: dict[Path, list[ET.Element]],
    feed_kinds: dict[str, str],
) -> int:
    """Second pass for TV series missing from the local EpGuides mirror.

    Re-downloads allshows.txt once, then re-attempts a link for every
    unresolved item: the real EpGuides page when the refreshed map resolves the
    series, otherwise an EpGuides site-search link so every series item stays
    linked. Returns the number of links added. Mutates the feed files it fixes.
    """
    if not epguides_misses:
        return 0
    unmatched = sum(len(items) for items in epguides_misses.values())
    print(
        f"  EpGuides: {unmatched} TV item(s) in {len(epguides_misses)} feed(s) "
        "unresolved - refreshing online list"
    )
    epguides_mapping = _refresh_epguides_mapping() or _epguides_map()
    total_added = 0
    for path, items in sorted(epguides_misses.items()):
        print(f"  [epguides] {path.stem}... ", end="", flush=True)
        added = 0
        fallback = 0
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            print("[ERR] unparseable feed")
            continue
        root = tree.getroot()
        # Re-parse replaced the element tree, so re-key items by guid/link to
        # find the node corresponding to each element collected during pass 1.
        item_by_id = {}
        for node in root.iter("item"):
            guid_el = node.find("guid")
            key = guid_el.text if guid_el is not None and guid_el.text else node.findtext("link")
            item_by_id.setdefault(key, node)
        for item in items:
            guid_el = item.find("guid")
            key = guid_el.text if guid_el is not None and guid_el.text else item.findtext("link")
            node = item_by_id.get(key)
            if node is None:
                continue
            title = node.findtext("title", "")
            kind = feed_kinds.get(path.name)
            is_tv_item = bool(EPISODE_TITLE_RE.search(title)) if kind is None else kind == "series"
            series = _epguides_series_title(title)
            if not (is_tv_item and series):
                continue
            slug = _find_epguides_slug(series, epguides_mapping)
            if _attach_epguides_link(node, series, epguides_mapping, allow_fallback=True):
                if slug:
                    added += 1
                else:
                    fallback += 1
        if added or fallback:
            tree.write(path, encoding="UTF-8", xml_declaration=True)
        total_added += added + fallback
        msg = f"epguides={added}"
        if fallback:
            msg += f" search={fallback}"
        print(f"[{'OK' if (added or fallback) else '--'}] {msg}")
    return total_added
