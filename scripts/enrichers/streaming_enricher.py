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
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import PTN

from core.tmdb import MovieInfo, find_by_imdb, movie_lookup, search_movie, search_tv, tv_lookup
from scripts.enrichers.player_servers import (
    DEFAULT_TORRENT_SERVERS,
    LEGACY_PLAYER_PAGE_BASE_URLS,
    PLAYER_PAGE_BASE_URL,
    PLAYER_SERVERS,
    build_player_page_link,
    build_player_page_url,
    season_episode,
)

FEEDS_DIR = Path(__file__).resolve().parent.parent.parent / "feeds"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

def _current_year() -> int:
    """Return the current year."""
    # Get the current year from the system date
    return date.today().year

# EpGuides publishes a TSV of every tracked series (title, directory/slug, …).
# We mirror it locally so enrich runs without a network round-trip per feed.
EPGUIDES_URL = "https://epguides.com/common/allshows.txt"
EPGUIDES_CACHE = REPO_ROOT / "data" / "allshows.txt"
EPGUIDES_TTL_SECONDS = 7 * 24 * 3600
# allshows.txt lags the site: a series EpGuides published a page for this week
# can be absent from the TSV for weeks. The recent-adds feed (small, frequent)
# carries the same title→page pairs for exactly those new shows, so it is
# mirrored too and merged into the map.
EPGUIDES_RECENT_URL = "https://epguides.com/menu/recentAddAll.xml"
EPGUIDES_RECENT_CACHE = REPO_ROOT / "data" / "epguides_recent.xml"
EPGUIDES_RECENT_TTL_SECONDS = 6 * 3600
# Fallback for a series with no EpGuides page at all: EpGuides' own site search,
# which is a Google Custom Search (its search page submits to this cx).
# `epguides.com/search/?q=…` must never be written — the page never reads the
# parameter, so it opens with an empty search box and no results.
EPGUIDES_CSE_URL = "https://www.google.com/cse"
EPGUIDES_CSE_CX = "006364566242780170875:hrcq-leun10"

# TMDb ids embedded in item URLs, in the shapes the sites actually use:
#   /movie/1234            /movie/1234-slug        (id first)
#   /movie/slug/1234                                (id last, own segment)
#   /movie/slug-1234                                (id last, same segment)
# and the same for /tv, /series, plus the plural /movies and /tv-shows that
# pontv uses. "series" appears in cinejoy detail URLs (/series/288673-carrie-2026).
#
# A slug-embedded id is required to be 5+ digits: a 4-digit tail is far more
# likely to be a release year ("/movie/verity-2026-rd1") than a TMDb id, and a
# wrong id is only caught later by _title_matches. Whole segments stay 4+
# because they cannot be a year in the same ambiguous position.
TMDB_ID_RE = re.compile(
    r"/(?P<media>movie|movies|tv|tv-shows|series)"
    r"(?:"
    r"/(?P<seg>\d{4,})(?:/|$|-)"
    r"|/[^/]*?-(?P<slug>\d{5,})(?:/|$|-)"
    r"|/[^/]+/(?P<deep>\d{4,})(?:/|$|-)"
    r")"
)
# All the non-movie spellings route to the TV lookup, matching _lookup's rule.
MEDIA_TYPE_BY_SEGMENT = {
    "movie": "movie",
    "movies": "movie",
    "tv": "tv",
    "tv-shows": "tv",
    "series": "tv",
}
# Matches URLs with a year in them (e.g. "the-box-2026" or "...-carrie-2026" at
# the end of the path -- hence the end-of-string alternative)
URL_YEAR_RE = re.compile(r"-(19\d{2}|20\d{2})(?:-|/|$)")
IMDB_ID_RE = re.compile(r"(tt\d{7,8})")
IMG_TAG_RE = re.compile(r'<img\s[^>]*>', re.IGNORECASE)
# Every href/src in an HTML fragment, so "does this description already link
# to X" can be answered by whole hosts instead of substrings.
URL_ATTR_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# Torrent episode titles carry SxxEyy (e.g. "The Gentlemen 2024 S02E03 …") or NxM
# signal that a title is a TV series, so we look up TMDb /search/tv, not movies.
EPISODE_TITLE_RE = re.compile(r"\bS\d{1,2}\s*E\d{1,2}\b|\b\d+x\d+\b", re.IGNORECASE)
# Season packs drop the episode number ("Time.2021.S03.1080p.…"), which leaves a
# bare Sxx marker as the only TV signal. When it is present the item must take
# the /search/tv path -- the movie path would query "Time" against TMDb's film
# index and come back as the wrong film ("About Time"), or nothing at all.
SEASON_MARK_RE = re.compile(r"\bS\d{1,2}\b", re.IGNORECASE)
# A feed with no sites.yaml entry states no `kind`, and its items may carry no
# SxxEyy marker either (bingebang lists episodes by name: "Dark Matter"). The
# item URL is then the last signal that the item is a series, which decides
# /search/tv vs /search/movie.
TV_DETAIL_URL_RE = re.compile(r"/(?:tv|series)/", re.IGNORECASE)
HAS_YEAR_RE = re.compile(r"\(\d{4}\)")
HAS_BARE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
YEAR_STRIP_RE = re.compile(r"[\(\[\{]\d{4}[\)\]\}]")
# Squishes words together (drops punctuation *and* whitespace) so a slug feed
# title ("the-fix") and its spaced TMDb title ("The Fix") compare equal. Also
# used by the episode-marker check, where the marker digits survive: only
# non-word characters are removed.
NON_WORD_RE = re.compile(r"[^\w]+")
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
    r"|\b(?:1080p|720p|2160p|4k|8k|uhd|\d+\s*fps|"
    r"bluray|brrip|web-?dl|webrip|hdrip|hdtv|"
    r"dvdrip|dvdscr|remux|camrip|bdrip|bdr|"
    r"pdtv|dsr|ppv|dvb|iptv|sdtv|tvrip|vhsrip|"
    r"x26[45]|h\.?26[45]|h\s+26[45]|26[45]|avc|hevc|av1|vp9|vp8|vc1|"
    r"mpeg2|mpeg4|xvid|divx|"
    r"ddp5?\.?1?|dd5|dts-?hd|dts|eac3|ac3|flac|aac|atmos|"
    r"true-?hd|lpcm|pcm|mp3|opus|ogg|vorbis|alac|wma|wav|aiff|ape|"
    r"dolby|digital|plus\d*|"
    r"mp4|mkv|avi|webm|m2ts|wmv|flv|"
    r"hdr10?(?:p|plus)?|hlg|sdr|hdr|dd|dv|dovi|ma|p7|"
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
    # `proper`/`repack` take a trailing counter on a second release
    # ("PROPER2"), and a bare `\bproper\b` did not match it, so the counter
    # survived into the query ("Fight Club PROPER2") and TMDb found nothing.
    r"rerip|readnfo|retail|proper\d*|repack\d*|"
    r"imax|sbs|interlaced|progressive|openmatte|anamorphic|hybrid|"
    r"bdremux|remux|pal|ntsc|lbxd|uhd|blu-?ray|blu|ray|"
    r"telesync|telecine|cam|hc|werk|dvd|remaster|remuxed|screener|"
    # The closing \b on this group is load-bearing, not decoration. The group
    # opened with \b, so without a matching one every alternative matched as a
    # *prefix* of a longer word: "blu" ate "Blue", "web" ate "Webb", "dd" ate
    # "Daddy", "ts" ate "Tshirt". Each truncation then queried TMDb with a
    # wrong title, so the film/series was missed or matched to the wrong poster.
    r"doxxi|nosecret|layer|dual|disc)\b"
    r"|\b5\s*1\b|\b7\s*1\b"
    # Subtitle and source tags. Whole tokens only: bare "sub" is an ordinary
    # title word, and the network shorthands are too short to strip safely, so
    # only the unambiguous multi-character forms are listed. Language tags are
    # deliberately absent: they are ordinary title words too ("The English
    # Patient", "The Italian Job") and are stripped separately, by position.
    r"|\b(?:esub|msub|bsub|hardsub|softsub|yts|yify|rarbg|eztv|subsplease|"
    r"tgx|paradox|remuxdoc|okko|gaste|rawhd)\b"
    # RIFE is a frame-interpolation tag, and scene groups pair it with the
    # "AI" modifier ("HEVC+AI.RIFE"); neither word belongs in a TMDb query.
    r"|\b(?:ai\s+)?rife\b"
    # "NOW" (the Now TV / Sky streaming service) is a source tag that scene
    # names put directly in front of the WEB marker, as in
    # "The.Fix.2026.2160p.NOW.WEB-DL.…". Unlike the other source tags above,
    # "now" is also an ordinary title word ("Apocalypse Now"), so it may be
    # stripped only in that source position: the lookahead demands a web tag
    # next (separators only), which a title word never has, while a resolution
    # or year between the two tokens ("Apocalypse Now 1979 1080p WEB-DL") does
    # not match either.
    r"|\bnow\b(?=[\s._-]*(?:web-?dl|webrip|web)\b)",
    re.IGNORECASE,
)


# Scene release language tags, stripped only in the tail that follows the
# release year -- which is where a scene name puts them ("Awarapan 2 2019 Hindi
# ESub"). They are also ordinary title words, and stripping them anywhere (the
# previous behaviour) queried TMDb for "The Patient" and "The Job" instead of
# "The English Patient" and "The Italian Job".
_LANG_TAG_RE = re.compile(
    r"(?:^|[.\s_])(?:hindi|tamil|telugu|kannada|bengali|malayalam|punjabi|"
    r"gujarati|urdu|korean|japanese|chinese|french|german|spanish|italian|"
    r"russian|dutch|arabic|hebrew|greek|turkish|english)(?=[.\s_]|$)",
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
        # r/SceneReleases wraps a release line's metadata in a trailing
        # parenthesis -- "(2160p)", "(2160p; 19.1 GiB)". Flattening the
        # bracket below exposes the resolution to the noise pass but leaves
        # the file size behind ("; 19 1 GiB"), and that residue trails the
        # "~GROUP" tag, so the tail-only tilde strip (see below) can no longer
        # reach it either. The query then read "Spider Man Brand New Day
        # iNTERNAL ~COD3D ; 19 1 GiB", which matches no TMDb title. Drop the
        # whole segment when it names a resolution or a size; a parenthesis
        # that is part of the name ("(500) Days of Summer") names neither, and
        # a release year in one is taken from the raw title, not from here.
        raw = re.sub(
            r"\s*\([^)]*(?:\d+[pi]\b|\d+(?:\.\d+)?\s*[GMKT]i?B)\b[^)]*\)\s*$",
            " ",
            raw,
            flags=re.IGNORECASE,
        )
        # A scene group can also sit inside a trailing parenthesis --
        # "… (2160p MA WEB-DL Hybrid H265 DV HDR DDP 5.1 English - HONE)" --
        # where the first SCENE_GROUP_RE pass, which needs the hyphen at the
        # very end of the string, cannot reach it. Drop the brackets so the
        # "-GROUP" is exposed, then strip it; the main pass removes the
        # brackets anyway, so this only moves the strip earlier.
        raw = re.sub(r"[()\[\]]", " ", raw)
        raw = SCENE_GROUP_RE.sub(" ", raw)
        # Language tags last, and only after the release year: a scene name puts
        # them in the tail ("Title 2026 1080p Hindi ESub"), a title keeps them
        # inside it ("The English Patient 1996"). See _LANG_TAG_RE.
        year_hits = list(BARE_YEAR_RE.finditer(raw))
        if year_hits:
            cut = year_hits[-1].end()
            raw = raw[:cut] + _LANG_TAG_RE.sub(" ", raw[cut:])
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

# Decorative media-type word after a parenthesized year ("Mutiny (2026)
# Film", "DC (2026) Film") on Romanian cinema sites' headings. Only the
# bracket-year + type shape is stripped, so a title that genuinely ends in
# "Film"/"Serie" (no year) is untouched.
_SITE_TYPE_SUFFIX_RE = re.compile(r"(?i)(\(\d{4}\))\s*(?:film|serie|serial)\b")

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
    # Pre-clean the Romanian " (2026) Film|Serie" heading decoration.
    raw = _SITE_TYPE_SUFFIX_RE.sub(r"\1", raw)
    # Pre-clean uindex-style titles (returns tuple of (cleaned_title, year, has_hyphens))
    raw, uindex_year, has_hyphens = _clean_uindex_title(raw)
    cleaned = _strip_release_noise(raw)
    if cleaned or not BARE_YEAR_RE.search(raw):
        # If uindex year was stripped, restore it
        if uindex_year and uindex_year not in cleaned and (
            f"({uindex_year})" in raw or f"({uindex_year})" in cleaned
        ):
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


def _preclean_site_title(raw: str) -> str:
    """Normalise the per-site title decorations both parsers would trip on.

    pontv headings ("7.2Swapped2026 • Adventure • Animation") and uindex's
    "Title (Year) …" form are site decorations, not release-name noise. The
    hand-written cleaner already knew about them; applying the same pre-clean
    before parse-torrent-title lets both parsers work on the same string.
    """
    raw = _clean_pontv_title(raw)
    raw = _SITE_TYPE_SUFFIX_RE.sub(r"\1", raw)
    raw, _, _ = _clean_uindex_title(raw)
    return raw


def _ptn_search_title(raw: str) -> str:
    """The TMDb query for a release name, from parse-torrent-title.

    PTN's rule engine is the primary parser: it knows the whole scene-tag
    vocabulary (sources, codecs, groups) and, unlike a blanket year strip, keeps
    a year the title itself carries, so "Blade Runner 2049 2021 1080p" and
    "2001: A Space Odyssey 1968" both survive it. That is also why the title is
    read as one unit here rather than by re-splitting at the first year.

    "" means "PTN found no usable title". A result that is *only* a year is
    rejected: PTN reads a heading built the other way round -- "DC (2026)" -- as
    the title "2026" with no year, and querying "2026" would only ever match a
    film by that name. The hand-written passes still handle that shape.
    """
    if not raw:
        return ""
    try:
        parsed = PTN.parse(_preclean_site_title(raw))
    except Exception:
        return ""
    title = (parsed.get("title") or "").strip()
    if not title or BARE_YEAR_RE.fullmatch(title):
        return ""
    return title


def _search_title_candidates(raw: str, *, is_tv: bool) -> list[str]:
    """TMDb queries to try, in order, for one release name.

    parse-torrent-title leads for both movies and series: it parses the name in
    one pass and handles the year-in-title case the hand-written cleaner had to
    be retried for. The hand-written result follows as a fallback -- it is what
    already worked for the site-specific shapes and for the rare name PTN
    reduces to a bare year. For a series the fallback is
    ``_series_search_title``, which first cuts at the SxxEyy marker so the
    episode title cannot leak into the query.
    """
    fallback = _series_search_title(raw) if is_tv else _clean_search_title(raw)
    candidates: list[str] = []
    for title in (_ptn_search_title(raw), fallback):
        if title and title not in candidates:
            candidates.append(title)
    return candidates


def _title_year(raw: str) -> str | None:
    """The release year a release name carries, or None.

    The *last* year-like token, not the first: a scene name closes with the
    release year ("Blade Runner 2049 2021 1080p…"), and a title that is itself
    a year ("1917 2019 …") puts its own year first, so the last token is the
    release year in both shapes. BARE_YEAR_RE bounds it, so a sequel number
    ("2049") is not mistaken for one.
    """
    matches = BARE_YEAR_RE.findall(raw or "")
    return matches[-1] if matches else None


def _cut_at_episode_marker(title: str) -> str:
    """Return the part of a release title before its SxxEyy / NxM marker.

    Everything from the marker on is the episode name plus quality flags, which
    TMDb does not index, so the head is the only part worth searching.
    """
    m = EPISODE_TITLE_RE.search(title)
    return title[: m.start()] if m else title


def _is_tv_title(title: str) -> bool:
    """A release title that names a series.

    The SxxEyy / NxM marker and a bare season marker ("S03") both signal one.
    The bare form covers season packs that drop the episode number, where the
    movie path would otherwise search a film index ("Time" -> "About Time").
    """
    return bool(EPISODE_TITLE_RE.search(title) or SEASON_MARK_RE.search(title))


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
# two-word or hyphenated forms, so none of them can match inside a title. The
# timing questions are the sole one-word verbs: "date" (plus "when …") names
# an availability question ("The odyssey bluray or webrip date?") and needs the
# REQUEST_OPENER_RE mark/opener to confirm, so a release whose title merely
# contains "date" without a question mark is still kept.
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
    r"released\s+weeks?\s+ago|any\s+(?:quality|region|place|site)|"
    r"when\s+(?:is|are|does|will)\b|when'?s\b|"
    r"(?:blu-?ray|web-?rip|dvd|uhd|4k|2160p|1080p)\s+(?:date|eta|out)\b|"
    r"release\s+(?:date|dvd|blu-?ray|eta)\b|\bdate\b(?=[?!,.]|$)"
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


# Trailing qualifier in an EpGuides title: it separates versions of the same
# series ("The Diplomat (US)" / "The Diplomat (UK)", "Doctor Who (1963)" /
# "Doctor Who (2005)"). Feeds rarely carry it, so it is indexed aside.
_TRAILING_QUALIFIER_RE = re.compile(r"\s*\([^()]*\)\s*$")
_EPGUIDES_MONTHS = {
    name: index
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def _strip_epguides_qualifier(title: str) -> str:
    """Drop a trailing qualifier: "The Diplomat (US)" -> "The Diplomat"."""
    return _TRAILING_QUALIFIER_RE.sub("", title).strip()


def _normalize_epguides_qualifier_key(title: str) -> str:
    """Normalized lookup key for a title with its trailing qualifier dropped."""
    return _normalize_epguides_title(_strip_epguides_qualifier(title))


def _epguides_air_rank(start: str, end: str) -> tuple[int, int, int]:
    """Rank two series sharing a bare title: still airing, then newest start.

    The version a feed means is the one on air now; when neither is, it is the
    newer incarnation ("The Office" is the US remake, not the UK original).
    A missing end ("___ ____") means still running, as EpGuides writes it for
    current shows; a missing start ranks oldest rather than as airing, since a
    date on record is what a year is parsed from.
    """
    airing = 0 if (end or "").strip().strip("_ ") else 1
    m = re.match(r"([A-Za-z]{3})\s*(\d{4})", (start or "").strip())
    month = _EPGUIDES_MONTHS.get(m.group(1).lower()[:3], 0) if m else 0
    year = int(m.group(2)) if m else 0
    return airing, year, month


def _fold_epguides_rows(rows: list[tuple[str, str, str, str]]) -> dict[str, str]:
    """Fold (title, slug, start date, end date) rows into a lookup map.

    Every title is indexed under its exact normalized form first; a title with a
    trailing qualifier is then *also* indexed under its bare form, which is what
    a feed actually carries ("The Diplomat" for "The Diplomat (US)"). A bare key
    an exact title already owns is never taken over, and one shared by several
    versions goes to the highest-ranked of them.
    """
    mapping: dict[str, str] = {}
    for title, slug, _start, _end in rows:
        mapping.setdefault(_normalize_epguides_title(title), slug)
    exact_keys = set(mapping)
    ranked: dict[str, tuple[int, int, int]] = {}
    for title, slug, start, end in rows:
        if _strip_epguides_qualifier(title) == title:
            continue
        key = _normalize_epguides_qualifier_key(title)
        if not key or key in exact_keys:
            continue
        rank = _epguides_air_rank(start, end)
        if key not in ranked or rank > ranked[key]:
            mapping[key] = slug
            ranked[key] = rank
    return mapping


def _load_epguides(path: Path = EPGUIDES_CACHE) -> dict[str, str]:
    """Load EpGuides allshows.txt → {normalized title: directory slug}."""
    rows: list[tuple[str, str, str, str]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                title = (row.get("title") or "").strip()
                slug = (row.get("directory") or "").strip()
                if title and slug:
                    rows.append(
                        (title, slug, row.get("start date") or "", row.get("end date") or "")
                    )
    except OSError:
        return {}
    return _fold_epguides_rows(rows)


def _load_recent_adds(path: Path = EPGUIDES_RECENT_CACHE) -> dict[str, str]:
    """Load the recent-adds RSS → {normalized title: directory slug}.

    Each item is a page link and the show's title; a show appears once per
    alias it has been filed under, all pointing at the same guide.
    """
    mapping: dict[str, str] = {}
    try:
        root = ET.fromstring(path.read_bytes())
    except (OSError, ET.ParseError):
        return mapping
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        m = re.search(r"epguides\.com/([^/?#]+)/?$", link)
        if title and m:
            mapping.setdefault(_normalize_epguides_title(title), m.group(1))
    return mapping


def _recent_adds_mapping() -> dict[str, str]:
    """Recent-adds map, re-downloaded when its mirror is older than its TTL.

    The feed is a few dozen kilobytes and changes exactly when a show is added,
    so it is refreshed on an hours-long TTL rather than allshows.txt's week.
    """
    cache = EPGUIDES_RECENT_CACHE
    if cache.exists() and time.time() - cache.stat().st_mtime < EPGUIDES_RECENT_TTL_SECONDS:
        return _load_recent_adds(cache)
    if not os.environ.get("EPGUIDES_DISABLE_DOWNLOAD"):
        try:
            resp = httpx.get(EPGUIDES_RECENT_URL, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(resp.content)
            return _load_recent_adds(cache)
        except Exception:
            pass
    return _load_recent_adds(cache) if cache.exists() else {}


def _with_recent_adds(mapping: dict[str, str]) -> dict[str, str]:
    """Merge the recent-adds feed into an allshows-derived map.

    allshows.txt entries win: it is the curated list, while the recent-adds
    feed repeats a show under every alias it is filed under.
    """
    for key, slug in _recent_adds_mapping().items():
        mapping.setdefault(key, slug)
    return mapping


def _epguides_mapping() -> dict[str, str]:
    """Return the EpGuides title→slug map, refreshing the local mirror when stale.

    The recent-adds feed is merged in on every path: a series it has a page for
    is not in allshows.txt yet, and without it that series falls through to the
    search link.
    """
    cache = EPGUIDES_CACHE
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < EPGUIDES_TTL_SECONDS:
            return _with_recent_adds(_load_epguides(cache))
    mapping = _load_epguides(cache) if cache.exists() else {}
    if not os.environ.get("EPGUIDES_DISABLE_DOWNLOAD"):
        try:
            resp = httpx.get(EPGUIDES_URL, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(resp.content)
            mapping = _load_epguides(cache)
        except Exception:
            pass
    return _with_recent_adds(mapping)


_EPGUIDES_MAP_CACHE: dict[str, str] | None = None


def _epguides_map() -> dict[str, str]:
    """Process-wide cached EpGuides map (downloaded once, reused across feeds)."""
    global _EPGUIDES_MAP_CACHE
    if _EPGUIDES_MAP_CACHE is None:
        _EPGUIDES_MAP_CACHE = _epguides_mapping()
    return _EPGUIDES_MAP_CACHE


def _refresh_epguides_mapping() -> dict[str, str] | None:
    """Re-download allshows.txt; refresh the local mirror only when it changed.

    Returns the fresh title→slug map (recent-adds feed merged in) when the
    online list differs from the local copy, else None. The caller then falls
    back to the cached map, which carries the recent-adds entries as well.
    Skips network when ``EPGUIDES_DISABLE_DOWNLOAD`` set.
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
        return _with_recent_adds(_load_epguides(cache))
    return None


def _find_epguides_slug(series_title: str, mapping: dict[str, str]) -> str | None:
    """Look up an EpGuides page slug for a series name (scene formatting tolerated).

    Exact titles are tried before qualifier-stripped ones, so a feed naming a
    specific version ("The Diplomat (UK)") gets that version's page while a bare
    "The Diplomat" still resolves through the index `_fold_epguides_rows` builds.
    """
    if not mapping or not series_title:
        return None
    candidates = [series_title]
    stripped = re.sub(r"^the\s+", "", series_title, flags=re.IGNORECASE)
    if stripped != series_title:
        candidates.append(stripped)
    keys = [_normalize_epguides_title(c) for c in candidates]
    keys += [_normalize_epguides_qualifier_key(c) for c in candidates]
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _build_epguides_link(slug: str) -> str:
    """Build the EpGuides link matching the existing trailer/IMDb link style."""
    return (
        f'<br><a href="https://epguides.com/{slug}/" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">EpGuides</b></a>'
    )


def _build_epguides_search_link(series_title: str) -> str:
    """Fallback link: EpGuides' own site search, for a series with no known page.

    ``epguides.com/search/?q=…`` is not usable: the page never reads its ``q``
    parameter, so it opens with an empty search box and no results. Its form
    submits to the Google Custom Search below instead, which prefills the query
    and returns the show's EpGuides page as the top hit.
    """
    q = quote(series_title, safe="")
    return (
        f'<br><a href="{EPGUIDES_CSE_URL}?cx={EPGUIDES_CSE_CX}&amp;q={q}" target="_blank" '
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


# An EpGuides link whose href is the site's own /search/?q= page, which was
# written as a fallback before it was known that the page never reads the
# parameter: readers of those feeds get an empty search box. Matches the anchor
# `_build_epguides_search_link` used to emit, leading <br> included so a
# rewrite leaves no stray separator behind.
_STALE_EPGUIDES_SEARCH_RE = re.compile(
    r'(?:<br>)?<a href="https://epguides\.com/search/\?[^"]*"[^>]*>\s*<b[^>]*>EpGuides</b>\s*</a>',
    re.IGNORECASE,
)

_CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _has_stale_epguides_link(item: ET.Element) -> bool:
    """True when an item still carries the dead EpGuides site-search link."""
    return any(
        _STALE_EPGUIDES_SEARCH_RE.search(item.findtext(tag) or "")
        for tag in ("description", _CONTENT_ENCODED)
    )


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
    after, it falls back to appending.

    An item already carrying the dead ``epguides.com/search/?q=`` link is
    rewritten to this link instead of being skipped, which is what repairs
    feeds published while that fallback was in use. Idempotent -- returns True
    when a link was added or replaced, and the next run finds neither a stale
    link nor a missing one.
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
    for node in (target, item.find(_CONTENT_ENCODED)):
        if node is None:
            continue
        text = node.text or ""
        if _STALE_EPGUIDES_SEARCH_RE.search(text):
            text = _STALE_EPGUIDES_SEARCH_RE.sub("", text)
            # A proper link alongside a stale one means the pair was written by
            # different runs; keep the proper one rather than duplicating it.
            node.text = (
                text if "EpGuides</b>" in text else _insert_after_imdb(text, eg_link)
            )
            added = True
        elif "EpGuides</b>" not in text:
            node.text = _insert_after_imdb(text, eg_link)
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


def _has_imdb_title_link(blob: str) -> bool:
    """Whether the description already links a direct IMDb title page.

    ``imdb.com/title/tt…/`` is the better form of the link (it opens the film
    the item is about, not a search page), so once an item carries one it is
    enriched and needs neither our search link nor an upgrade.
    """
    if not _IMDB_LABEL_RE.search(blob or ""):
        return False
    return any(
        _host_is(host, "imdb.com") and re.match(r"/title/tt\d+", path)
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



def _build_tmdb_link(
    tmdb_id: int | str | None, media_type: str | None, title: str | None
) -> str:
    """Build a direct TMDb page link, or a title search if no id is known."""
    if tmdb_id and media_type in ("movie", "tv") and str(tmdb_id).isdecimal():
        href = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
    elif title:
        href = 'https://www.themoviedb.org/search?query=' + __import__('urllib').parse.quote(title, safe='')
    else:
        return ""
    return '<a href="' + href + '" target="_blank" rel="noopener noreferrer"><b style="color:#01b4e4;">TMDb</b></a>'


# An IMDb anchor in a description: site-provided (/find or /title) or the one
# this module writes. The TMDb link is placed directly below it.
_IMDB_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href="[^"]*imdb\.com[^"]*"[^>]*>.*?</a>',
    re.IGNORECASE | re.DOTALL,
)

# A TMDb anchor: the direct page this module writes, or a site's search link.
# Used to repair an item published with TMDb above IMDb.
_TMDB_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href="[^"]*themoviedb\.org[^"]*"[^>]*>.*?</a>',
    re.IGNORECASE | re.DOTALL,
)


def _last_imdb_anchor_end(blob: str) -> int | None:
    """End offset of the last IMDb anchor in `blob`, or None when there is none."""
    last_end = None
    for match in _IMDB_ANCHOR_RE.finditer(blob or ""):
        last_end = match.end()
    return last_end


def _insert_after_imdb_anchor(blob: str, fragment: str) -> str:
    """Place ``<br>fragment`` immediately after the last IMDb anchor in `blob`.

    The TMDb link belongs directly below IMDb, and the IMDb anchor it should
    follow is not always the one this run writes: a site-provided anchor can
    sit after the block inserted below the poster, so appending TMDb to that
    block would put it above IMDb. With no IMDb anchor at all the fragment goes
    to the end, which is still below every link already there.
    """
    last_end = _last_imdb_anchor_end(blob)
    if last_end is None:
        return (blob or "") + "<br>" + fragment
    return blob[:last_end] + "<br>" + fragment + blob[last_end:]


def _move_tmdb_under_imdb(blob: str) -> str:
    """Move a TMDb anchor that sits above IMDb to directly below it.

    Written below IMDb now, the link was above it in items the older block
    enriched: that block went under the poster while the site's own IMDb anchor
    came later in the description. Repairing the position here is what lets the
    published feeds converge; once the TMDb anchor is below the IMDb one this is
    a no-op, so re-runs leave the bytes alone. Returns `blob` unchanged when
    there is nothing to move.

    An item carrying more than one TMDb anchor above IMDb keeps only the first:
    every one of them has to end up below IMDb or the anchor left behind would
    re-trigger this repair on the next run, so the item would never converge.
    """
    imdb_end = _last_imdb_anchor_end(blob)
    if imdb_end is None:
        return blob or ""
    kept: list[str] = []
    moved = ""
    pos = 0
    for match in _TMDB_ANCHOR_RE.finditer(blob):
        if match.start() >= imdb_end:
            break
        moved = moved or match.group(0)
        start, end = match.start(), match.end()
        # Drop one separator with the anchor so the remaining links keep a
        # single <br> between them.
        if blob[max(0, start - 4):start].lower() == "<br>":
            start -= 4
        elif blob[end:end + 4].lower() == "<br>":
            end += 4
        kept.append(blob[pos:start])
        pos = end
    if not moved:
        return blob
    kept.append(blob[pos:])
    moved_out = "".join(kept)
    imdb_end = _last_imdb_anchor_end(moved_out)
    return moved_out[:imdb_end] + "<br>" + moved + moved_out[imdb_end:]


def _has_tmdb_link(blob: str) -> bool:
    """Whether a labelled TMDb anchor already links to a movie/TV page."""
    if not re.search(r"TMDb</b>", blob or "", re.IGNORECASE):
        return False
    for host, path in _links(blob):
        segments = path.strip("/").split("/")
        if not _host_is(host, "themoviedb.org") or not segments:
            continue
        if segments[0] == "search" or (
            segments[0] in ("movie", "tv")
            and len(segments) > 1
            and segments[1].isdecimal()
        ):
            return True
    return False

def _build_imdb_title_link(imdb_id: str) -> str:
    """Build a direct IMDb title link for a known IMDb id."""
    return (
        f'<a href="https://www.imdb.com/title/{imdb_id}/" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">IMDb</b></a>'
    )


# The href of an IMDb *search* anchor, so an item enriched when only the search
# link existed can be upgraded in place to a direct title link rather than
# carrying both. Only the URL is rewritten; the label and attributes stay put.
_IMDB_FIND_HREF_RE = re.compile(
    r'(<a\s[^>]*href=")https?://(?:www\.)?imdb\.com/find/?\?[^"]*(")',
    re.IGNORECASE,
)


def _upgrade_imdb_anchor(text: str, imdb_id: str) -> str:
    """Rewrite an existing IMDb ``/find`` href to ``/title/tt…/``.

    Returns the text unchanged when there is nothing to upgrade, so callers can
    compare for a real write.
    """
    if not text or not imdb_id:
        return text
    return _IMDB_FIND_HREF_RE.sub(
        rf"\g<1>https://www.imdb.com/title/{imdb_id}/\g<2>", text
    )


# The ▶ Play anchor's href, in every base URL it was ever published with. Items
# published before the player page could take an episode carry `type=tv` with no
# `season`/`episode`, a short-lived form sent the release year as its own `year=`
# parameter (which the page ignores), and links published while the player lived
# on the user site name that path (`LEGACY_PLAYER_PAGE_BASE_URLS`). All of them
# are rewritten in place to the project-site URL, the same way a `/find?` IMDb
# link is upgraded.
_ALL_PLAYER_BASES = (PLAYER_PAGE_BASE_URL, *LEGACY_PLAYER_PAGE_BASE_URLS)
_PLAYER_PAGE_HREF_RE = re.compile(
    r'(?P<pre><a\s[^>]*href=")'
    + r'(?P<base>' + "|".join(re.escape(base) for base in _ALL_PLAYER_BASES) + r")"
    + r"(?P<query>\?[^\"]*)(?P<post>\")",
    re.IGNORECASE,
)
# The paths those bases live at, for the check that a matched href really is the
# player page and not something that merely starts the same way.
_PLAYER_PAGE_PATHS = frozenset(
    urlsplit(base).path.rstrip("/") for base in _ALL_PLAYER_BASES
)


def _has_player_anchor(text: str) -> bool:
    """Whether `text` already carries a ▶ Play link, in any published spelling."""
    return any(base in text for base in _ALL_PLAYER_BASES)


def _player_page_href(
    href: str,
    *,
    season: int | None,
    episode: int | None,
    year: str | None,
    epguides_slug: str | None,
) -> str:
    """The player-page href `href` should carry, rebuilt in the current spelling.

    The identifiers and media type are taken from the existing link, everything
    else from the item, so the rewrite never invents a title for an item that had
    none. The rebuild goes through the writer's own
    :func:`player_servers.build_player_page_url`, so the fresh-write path and this
    one cannot disagree. Returns `href` untouched when it is already in this
    spelling — which is what makes the upgrade converge instead of rewriting on
    every run — or when the link names no id at all, because then there is
    nothing to build from.
    """
    parts = urlsplit(href)
    if parts.path.rstrip("/") not in _PLAYER_PAGE_PATHS:
        return href
    # `parse_qs` already percent-decodes; the values go back through
    # `build_player_page_url`, which re-quotes them, so a decoded `<` or `"` can
    # never close the href attribute it is written into.
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}

    title = query.get("title") or None
    # The EpGuides mirror is downloaded at run time and its load failure is
    # swallowed, so a run can legitimately miss a slug a previous run resolved.
    # Keeping the published slug in that case avoids stripping a working
    # parameter and re-adding it on the next run.
    slug = epguides_slug or query.get("epguides") or None

    return build_player_page_url(
        query.get("tmdb") or None,
        query.get("imdb") or None,
        query.get("type") or None,
        title,
        season=season,
        episode=episode,
        year=year,
        epguides_slug=slug,
    ) or href


def _upgrade_player_anchor(
    text: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    year: str | None = None,
    epguides_slug: str | None = None,
) -> str:
    """Rewrite every ▶ Play href in `text` to the current base URL and spelling.

    Unchanged text means nothing to upgrade, so the caller compares for a real
    write and the skip guard below reads the same answer.
    """
    if not _has_player_anchor(text):
        return text

    def replace(match: re.Match[str]) -> str:
        prefix, suffix = match["pre"], match["post"]
        built = _player_page_href(
            f"{match['base']}{match['query']}",
            season=season,
            episode=episode,
            year=year,
            epguides_slug=epguides_slug,
        )
        return f"{prefix}{built}{suffix}"

    return _PLAYER_PAGE_HREF_RE.sub(replace, text)


def _build_trailer_link(title: str, year: str | None) -> str:
    """Build a YouTube trailer search link, matching the format used by site description selectors."""
    # Always include year if available
    query = f"{title} ({year})" if year and not re.search(r'\b(?:19|20)\d{2}\b', title) else title
    q = quote(query, safe="")
    return (
        f'<a href="https://www.youtube.com/results?search_query='
        f'{q}+preview%7Cpromo%7Ctrailer+-fake+-fan&amp;sp=EgIYAQ%253D%253D" '
        f'target="_blank" rel="noopener noreferrer">'
        f'<b style="color:#6600cc;">Trailer</b></a>'
    )


def _append_player_links(
    targets: list[ET.Element], links: list[tuple[str, str]]
) -> int:
    """Append ``(anchor, guard_label)`` pairs to each target, each once.

    The guard label is the rendered link text (``"VidSrc</b>"``), so a re-run
    that already carries the link leaves it alone -- this is what keeps the
    enrichment idempotent across the hourly CI runs. Targets with no text yet
    are skipped: there is nothing to append to. Returns the number of links
    actually written, for the caller's stats.
    """
    written = 0
    for el in targets:
        if not el.text:
            continue
        for link, guard in links:
            if guard not in el.text:
                el.text = el.text + "<br>" + link
                written += 1
    return written


# Spelling folds for the title guard. A feed name and the TMDb record of the
# same film can spell the same word differently: "Minions.And.Monsters" vs the
# TMDb title "Minions & Monsters", or "The.Fantastic.Four.First.Steps" vs TMDb's
# "The Fantastic 4: First Steps". Without the folds a real release fails the
# guard and stays unenriched. Only "&" and single digits are rewritten: digits
# also live in years, resolutions and codecs, but a word a digit-expanded form
# could falsely line up with is itself the same title, and keeping an item is
# this guard's deliberate bias (a missed poster costs more than a lookup).
_DIGIT_SPELLINGS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def _title_guard_variants(title: str) -> list[str]:
    """Squished spellings of a title compared by :func:`_title_matches`."""
    lowered = title.lower()
    base = NON_WORD_RE.sub("", lowered)
    with_and = NON_WORD_RE.sub("", lowered.replace("&amp;", "and").replace("&", "and"))
    plain_digits = "".join(_DIGIT_SPELLINGS.get(ch, ch) for ch in base)
    amp_digits = "".join(_DIGIT_SPELLINGS.get(ch, ch) for ch in with_and)
    return list(dict.fromkeys([base, with_and, plain_digits, amp_digits]))


def _title_matches(tmdb_title: str, feed_title: str) -> bool:
    """Check if TMDb title validates against feed title to avoid wrong-ID lookups."""
    if not tmdb_title or not feed_title:
        return True
    a_variants = _title_guard_variants(tmdb_title)
    b_variants = [YEAR_STRIP_RE.sub("", v).strip() for v in _title_guard_variants(feed_title)]
    if not any(a_variants) or not any(b_variants):
        return True
    for a in a_variants:
        for b in b_variants:
            if a in b or b in a or any(w in b for w in a.split() if len(w) > 3):
                return True
    return False


def _tmdb_id_match(m: re.Match) -> tuple[str, int] | None:
    """Pull (media_type, id) out of a TMDB_ID_RE match, whichever alternative hit."""
    for name in ("seg", "slug", "deep"):
        value = m.group(name)
        if value:
            return (MEDIA_TYPE_BY_SEGMENT[m.group("media")], int(value))
    return None


def _extract_tmdb_id(link: str) -> tuple[str, int] | None:
    m = TMDB_ID_RE.search(link)
    if m:
        return _tmdb_id_match(m)
    return None


def _lookup(media_type: str, tmdb_id: int):
    if media_type == "movie":
        return movie_lookup(tmdb_id)
    return tv_lookup(tmdb_id)


# --- Fallback search when TMDb's own search misses -------------------------
#
# A cleaned scene name is not always a title TMDb indexes: a 2026 film can sit
# in TMDb under a colon ("Insidious: Out of the Further") where the release
# name has a dash, and TMDb search can simply return nothing for the exact
# spelling. The manual fix is to search IMDb for the release name, take the
# film's IMDb id, and open its TMDb page; these helpers do that in order of
# accuracy:
#
#   1. IMDb's own suggestion endpoint -- free, no key, and it resolved both
#      "Insidious Out of the Further" and "Matchbox the Movie" to their 2026
#      entries, where OMDb's index still returned nothing (or a stale 1996 hit
#      for "Oasis: Don't Look Back in Anger").
#   2. OMDb (OMDB_API_KEY) -- a proper API with a free daily quota, kept as the
#      first *keyed* fallback and the only one that survives an IMDb change.
#   3. Google, last resort (see _google_tmdb_id).
#
# A Google result page carries the target URLs in /url?q=... redirects and in
# the visible href attributes; TMDB_ID_RE matches the /movie/ID or /tv/ID path
# wherever it appears, so one pass over the raw HTML finds the first hit.
_GOOGLE_SEARCH_URL = "https://www.google.com/search"
_GOOGLE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_IMDB_SUGGEST_URL = "https://v3.sg.media-imdb.com/suggestion/x"
_OMDB_URL = "https://www.omdbapi.com/"

# qid is IMDb's type tag. Only screen entries are useful, and "tvSeries" is the
# spelling for a series -- a filter that expected "tv" silently skipped every
# one of them.
_IMDB_MOVIE_QIDS = {"movie", "tvMovie"}
_IMDB_SERIES_QIDS = {"tvSeries", "tvMiniSeries"}


def _pick_imdb_hit(candidates: list[dict], year: str | None, prefer: str | None):
    """Choose the best IMDb/OMDb candidate: preferred type, then matching year.

    `prefer` is "movie" or "tv" from the feed item's own shape. A matching year
    is the stronger signal (remakes and same-title films), but the year is a
    preference and not a filter: when nothing carries it, the type-only pool
    still resolves a film whose release date is filed one year off.
    """
    def is_type(c: dict) -> bool:
        if prefer == "movie":
            return c["qid"] in _IMDB_MOVIE_QIDS
        if prefer == "tv":
            return c["qid"] in _IMDB_SERIES_QIDS
        return True

    for pool in (
        [c for c in candidates if (not year or c["year"] == year) and is_type(c)],
        [c for c in candidates if is_type(c)],
        candidates,
    ):
        if pool:
            c = pool[0]
            return (c["id"], c["title"], c["year"])
    return None


def _imdb_candidates(data: dict) -> list[dict]:
    """Screen candidates in an IMDb suggestion response, in response order.

    Only film/series qids are kept: "The Show (podcast)" comes back as
    TVEpisode, and an entry without an ``tt`` id or a label cannot be
    resolved at all.
    """
    candidates: list[dict] = []
    for entry in data.get("d", []):
        qid = entry.get("qid") or ""
        imdb_id = entry.get("id") or ""
        label = (entry.get("l") or "").strip()
        if qid not in _IMDB_MOVIE_QIDS | _IMDB_SERIES_QIDS:
            continue
        if not imdb_id.startswith("tt") or not label:
            continue
        candidates.append(
            {
                "id": imdb_id,
                "title": label,
                "year": str(entry["y"]) if entry.get("y") else None,
                "qid": qid,
            }
        )
    return candidates


def _imdb_year_hit(
    candidates: list[dict], year: str, prefer: str | None
) -> tuple[str, tuple[str, str, str] | None]:
    """Decide what one year of the backwards walk yields.

    Returns ``("hit", record)`` for exactly one candidate filed under `year`
    with the preferred type, ``("stop", None)`` when the title exists at that
    year only as the wrong medium (an older film of the same name is a
    different title, not a release-date mismatch), and ``("advance", None)``
    otherwise: nobody at that year, several distinct titles there (ambiguous),
    or a preferred-type candidate filed under an older year worth walking to.
    """
    def is_type(c: dict) -> bool:
        if prefer == "movie":
            return c["qid"] in _IMDB_MOVIE_QIDS
        if prefer == "tv":
            return c["qid"] in _IMDB_SERIES_QIDS
        return True

    exact = [c for c in candidates if c["year"] == year and is_type(c)]
    if len({c["id"] for c in exact}) == 1:
        c = exact[0]
        return ("hit", (c["id"], c["title"], c["year"]))
    if any(is_type(c) for c in candidates):
        return ("advance", None)
    if candidates:
        return ("stop", None)
    return ("advance", None)


def _imdb_suggest(title: str, year: str | None = None, prefer: str | None = None):
    """Search IMDb's suggestion endpoint. Returns (imdb_id, title, year)|None.

    With an explicit `year` this is a single request: the endpoint already
    ranks ``title year`` itself, and walking back from it would only spend
    requests on other years.

    Without a year the walk starts at the current year and goes back one year
    at a time, because a fresh release is filed under the year it comes out
    while the item's own title may carry no year at all. A year whose only
    same-title entry is the wrong medium stops the walk: an older film with the
    same name is a different title, not the same release dated one year off.
    """
    if not title:
        return None

    if year is not None:
        try:
            resp = httpx.get(
                f"{_IMDB_SUGGEST_URL}/{quote(f'{title} {year}')}.json",
                timeout=10,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        return _pick_imdb_hit(_imdb_candidates(data), year, prefer)

    current_year = _current_year()
    for y in range(current_year, current_year - 10, -1):
        try:
            resp = httpx.get(
                f"{_IMDB_SUGGEST_URL}/{quote(f'{title} {y}')}.json",
                timeout=10,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue
        action, hit = _imdb_year_hit(_imdb_candidates(data), str(y), prefer)
        if action == "hit":
            return hit
        if action == "stop":
            return None

    # Nothing matched year by year: one last broad search, where any year is
    # acceptable (this is also the only form that works for a title IMDb files
    # under a year outside the walked window).
    try:
        resp = httpx.get(
            f"{_IMDB_SUGGEST_URL}/{quote(title)}.json",
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    return _pick_imdb_hit(_imdb_candidates(data), None, prefer)


def _omdb_search(title: str, year: str | None = None, prefer: str | None = None):
    """Search OMDb for `title`. Returns (imdb_id, title, year)|None.

    OMDb's exact-title lookup (``?t=``) is tried first -- it is the only form
    that accepts a year -- and its search endpoint (``?s=``) second. OMDb's
    index lags for brand-new releases, which is why this is the fallback behind
    IMDb's own suggestion endpoint and not the first choice.
    """
    api_key = os.environ.get("OMDB_API_KEY")
    if not api_key or not title:
        return None
    preferred_type = {"movie": "movie", "tv": "series"}.get(prefer or "")
    attempts: list[dict] = [{"t": title}]
    if year:
        attempts.insert(0, {"t": title, "y": year})
    attempts.append({"s": title})
    for params in attempts:
        try:
            resp = httpx.get(_OMDB_URL, params={**params, "apikey": api_key}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        if data.get("Response") == "False":
            continue
        rows = data.get("Search") or ([data] if data.get("imdbID") else [])
        candidates: list[dict] = []
        for row in rows:
            imdb_id = row.get("imdbID") or ""
            label = (row.get("Title") or "").strip()
            if not imdb_id.startswith("tt") or not label:
                continue
            candidates.append(
                {
                    "id": imdb_id,
                    "title": label,
                    "year": (row.get("Year") or "")[:4] or None,
                    "qid": (row.get("Type") or "").lower(),
                }
            )
        if preferred_type:
            typed = [c for c in candidates if c["qid"] == preferred_type]
            candidates = typed or candidates
        if candidates:
            c = candidates[0]
            return (c["id"], c["title"], c["year"])
    return None


def _omdb_by_id(imdb_id: str):
    """Canonical OMDb record for an IMDb id, the ``?i=`` form.

    Returns ``(title, year, media_kind, poster)`` or None. ``media_kind`` uses
    TMDb's spelling ("movie"/"tv") and ``poster`` is OMDb's image URL or None.
    This is the id-keyed lookup the OMDb docs lead with: the right tool for an
    id TMDb's /find does not know yet. ``_fallback_lookup`` uses the record's
    canonical title for one more TMDb search, and falls back to its poster so a
    brand-new or obscure release is not dropped when TMDb has nothing at all.
    """
    api_key = os.environ.get("OMDB_API_KEY")
    if not api_key or not imdb_id:
        return None
    try:
        resp = httpx.get(_OMDB_URL, params={"i": imdb_id, "apikey": api_key}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if data.get("Response") != "True":
        return None
    title = (data.get("Title") or "").strip()
    if not title:
        return None
    year = (data.get("Year") or "")[:4] or None
    kind = "tv" if (data.get("Type") or "").lower() == "series" else "movie"
    poster = data.get("Poster") or None
    if poster == "N/A":
        poster = None
    return (title, year, kind, poster)


def _google_tmdb_id(title: str, year: str | None = None) -> tuple[str, int] | None:
    """Find a TMDb page for `title` via Google. Returns (media_type, tmdb_id).

    The manual method, kept last: Google the release name + "tmdb" and open the
    first TMDb result. Google answers datacenter addresses with a consent page
    or a CAPTCHA, so it is expected to fail from CI -- the IMDb/OMDb layers
    above are the reliable path. It costs nothing when they succeed.
    """
    query = f"{title} {year or ''} tmdb".strip()
    try:
        resp = httpx.get(
            _GOOGLE_SEARCH_URL,
            params={"q": query, "num": "10"},
            headers=_GOOGLE_HEADERS,
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        return None
    for m in TMDB_ID_RE.finditer(resp.text):
        hit = _tmdb_id_match(m)
        if hit:
            return hit
    return None


def _fallback_lookup(title: str, year: str | None, prefer: str | None):
    """Resolve `title` through IMDb -> OMDb -> Google when TMDb search misses.

    An IMDb/OMDb hit becomes a TMDb result two ways: TMDb's own ``/find`` by
    IMDb id (exact, and it fills poster/year), and -- when that finds nothing --
    a TMDb search of the corrected title, which is the "search IMDb, then
    search TMDb with the right title" step done by hand. A hit OMDb still knows
    by id (``?i=``) is re-spelled once more when /find and the corrected search
    both miss, and OMDb's own poster is the last resort for an id TMDb has no
    record for at all. Returns a MovieInfo with a poster, or None.
    """
    for hit in (_imdb_suggest(title, year, prefer), _omdb_search(title, year, prefer)):
        if not hit:
            continue
        imdb_id, correct_title, correct_year = hit
        info = find_by_imdb(imdb_id)
        if info and info.poster_url:
            info.imdb_id = imdb_id
            return info
        if correct_title:
            info = (search_tv if prefer == "tv" else search_movie)(
                correct_title, year=correct_year
            )
            if info and info.poster_url:
                info.imdb_id = imdb_id
                return info
        omdb = _omdb_by_id(imdb_id)
        if not omdb:
            continue
        omdb_title, omdb_year, omdb_kind, omdb_poster = omdb
        if omdb_title != correct_title:
            # OMDb's by-id record is the canonical spelling for an id /find does
            # not resolve; ?t=/?s= can return a looser label.
            searcher = (
                search_tv
                if (prefer == "tv" or omdb_kind == "tv")
                else search_movie
            )
            info = searcher(omdb_title, year=omdb_year)
            if (not info or not info.poster_url) and omdb_year:
                info = searcher(omdb_title, year=None)
            if info and info.poster_url:
                info.imdb_id = imdb_id
                return info
        if omdb_poster:
            # TMDb has no record for the id at all: enrich from OMDb alone
            # (poster, canonical title, year, direct IMDb link). The caller's
            # _title_matches guard still decides whether it belongs on the item.
            return MovieInfo(
                poster_url=omdb_poster,
                year=omdb_year,
                title=omdb_title,
                media_type=omdb_kind,
                imdb_id=imdb_id,
            )
    tmdb_hit = _google_tmdb_id(title, year)
    if tmdb_hit:
        info = _lookup(*tmdb_hit)
        if info and info.poster_url:
            return info
    return None


def _lookup_link(link: str):
    """Try TMDb ID first, then IMDb ID fallback.

    A dead id (404, or a site that encodes its *own* ids in ``/movie/5…`` URLs
    like hydrahd) resolves to an empty MovieInfo. That emptiness is a miss, not
    an answer: the caller treats `None` as "search by title", and an apparently
    alive-but-empty result was silently dead-ending those items -- no search,
    no links, no upgrade -- because `None` and "empty" were different things.
    """
    id_info = _extract_tmdb_id(link)
    if id_info:
        info = _lookup(*id_info)
        if info.title or info.poster_url:
            return info
        return None
    m = IMDB_ID_RE.search(link)
    if m:
        info = find_by_imdb(m.group(1))
        if info.title or info.poster_url:
            return info
        return None
    return None


def process_feed(
    path: Path,
    epguides_mapping: dict[str, str] | None = None,
    epguides_misses: dict | None = None,
    is_series_feed: bool | None = None,
    feed_category: str | None = None,
    keep_titles: bool = False,
    player_servers: tuple[str, ...] | None = None,
) -> tuple[bool, dict]:
    """Process a single feed file. Returns (changed, stats).

    ``epguides_mapping`` is the normalized-title→slug map; when omitted it is
    loaded (and downloaded when stale) through ``_epguides_map()``. When a TV
    series in the feed is missing from the map, ``epguides_misses`` (if given)
    records its feed path so the caller can retry after an online refresh.

    ``is_series_feed`` comes from the site config's ``kind`` field: True for a
    series feed, False for a movie feed. When None, a per-item title heuristic
    (SxxEyy / NxM marker) decides whether EpGuides links apply.

    ``keep_titles`` (site config ``keep_titles: true``) publishes each title
    exactly as scraped: no TMDb replacement and no appended year. Torrent feeds
    need this because their names already carry the year and release tags the
    reader wants.

    ``player_servers`` controls which streaming embed links are appended to each
    item. ``None`` applies the per-category default (cinesrc + vidsrc + vidlink
    for torrents/releases; none for other categories). ``[]`` disables player links
    for this feed regardless of category. A tuple of server ids overrides the default.
    """
    stats: dict = {"items": 0, "posters": 0, "years": 0, "future": 0, "errors": 0, "skipped": 0, "links": 0, "epguides": 0}
    # Resolve the map when the caller omits it. Without this, the `if ... and
    # epguides_mapping` guard below is False and EpGuides linking is silently
    # skipped for every item - the documented default has to actually load it.
    if epguides_mapping is None:
        epguides_mapping = _epguides_map()
    # Determine if this is a torrent/release feed that should get player links
    is_torrent_feed = False
    if feed_category:
        is_torrent_feed = feed_category in ("torrents", "releases")
    else:
        # Fallback: check common names
        fname = path.name.lower()
        if "torrent" in fname or "scene" in fname or "release" in fname or "uindex" in fname:
            is_torrent_feed = True
    # Resolve the effective player server list:
    # None → per-category default; [] → disable entirely; explicit tuple → override
    if player_servers is None:
        effective_servers: tuple[str, ...] = (
            DEFAULT_TORRENT_SERVERS if is_torrent_feed else ()
        )
    else:
        effective_servers = player_servers
    # Fail loudly on a server id that does not exist in the registry rather
    # than silently emitting no links for it. Configured ids are validated at
    # load time (core/config.py), so this only fires on a direct caller.
    unknown_servers = {sid for sid in effective_servers if sid not in PLAYER_SERVERS}
    if unknown_servers:
        raise ValueError(
            f"unknown player_server(s) {sorted(unknown_servers)}; known: "
            f"{sorted(PLAYER_SERVERS)}"
        )
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
        link_url = (link_el.text if link_el is not None else "") or ""
        is_tv = (
            _is_tv_title(title_text)
            or bool(TV_DETAIL_URL_RE.search(link_url))
            if is_series_feed is None
            else is_series_feed
        )
        series_search_title = _epguides_series_title(title_text) if is_tv and title_text else ""
        epguides_slug = None
        if series_search_title and epguides_mapping:
            epguides_slug = _find_epguides_slug(series_search_title, epguides_mapping)
        # An item whose only link is the dead site-search one counts as
        # unresolved: it has no working link, so the refresh pass must get a
        # chance at the real page (and repairs it if it still cannot).
        has_stale_epguides = _has_stale_epguides_link(item)
        description = item.findtext("description", "") or ""
        is_miss = (
            is_tv and series_search_title and not epguides_slug
            and (has_stale_epguides or "EpGuides</b>" not in description)
        )
        if is_miss and epguides_misses is not None:
            epguides_misses.setdefault(path, []).append(item)
        elif epguides_slug and _attach_epguides_link(item, series_search_title, epguides_mapping):
            stats["epguides"] += 1
            changed = True

        # Already-enriched items (poster + IMDb link present) need no further
        # TMDb lookups. Skip the API round-trip; epguides was handled above.
        #
        # The exception is a title with no year in it: noctratv ships bare names
        # ("Black Hawk Down") built from `img/@alt`, and because its description
        # already carried a TMDb poster and an IMDb link, this continue fired on
        # all 50 items every run -- the feed never gained the "(2001)" every
        # other feed shows. So the lookup is still skipped when there is nothing
        # left to add, and made when the year is missing.
        existing_enriched = item.findtext("description", "") or ""
        already_enriched = _has_host(existing_enriched, "image.tmdb.org") and (
            _has_path(existing_enriched, "imdb.com", "/find")
            or _has_imdb_title_link(existing_enriched)
        )
        # What an already-enriched item can still be missing is the ▶ Play link
        # itself. This replaces a guard on the configured servers' label tags:
        # per-server anchors are no longer written (the player page builds them),
        # so that clause could never settle and every enriched torrent item spent
        # a TMDb call every hour for nothing. Any published spelling counts as
        # present, or a legacy item would gain a second anchor beside the one the
        # upgrade is already fixing.
        needs_player_link = not _has_player_anchor(existing_enriched)
        # An item enriched when only the /find search link existed is upgraded
        # in place to a direct /title/tt…/ link once its IMDb id is known. Like
        # CineSrc, this is a one-time upgrade: the next run sees the title link
        # and skips.
        needs_imdb_title = (
            _has_imdb_search_link(existing_enriched)
            and not _has_imdb_title_link(existing_enriched)
        )
        # A ▶ Play link written before the player page could take an episode (or
        # while the year went out as a `year=` parameter it ignores) is rewritten
        # in place. One-time like the IMDb upgrade: `_upgrade_player_anchor`
        # rebuilds the href deterministically, so once it matches, the next run
        # gets the same string back and this is False.
        #
        # The year here is the title's own, because deciding this is a lookup's
        # whole job. Once a TMDb year is known it wins (the writer passes
        # `info.year or player_year`), which rewrites the href once more — and
        # that href is already final, because a folded year suppresses further
        # folding. So the disagreement costs one converging pass, never a flip.
        marker = season_episode(title_text) if is_tv else None
        player_season, player_episode = (None, None) if marker is None else marker
        player_year = _title_year(title_text)
        needs_player_upgrade = (
            _has_player_anchor(existing_enriched)
            and _upgrade_player_anchor(
                existing_enriched,
                season=player_season,
                episode=player_episode,
                year=player_year,
                epguides_slug=epguides_slug,
            )
            != existing_enriched
        )
        # An item enriched by the older link block carries TMDb above IMDb.
        # The link guard means nothing would rewrite it, so the skip below has
        # to let it through for the move -- one pass, then it converges.
        needs_tmdb_order = _move_tmdb_under_imdb(existing_enriched) != existing_enriched
        if (
            already_enriched
            and (has_year or has_bare_year)
            and not needs_player_link
            and not needs_imdb_title
            and not needs_player_upgrade
            and not needs_tmdb_order
        ):
            continue

        info = _lookup_link(link_el.text)
        if (
            info is not None
            and info.title
            and title_text
            and _title_matches(info.title, title_text) is False
        ):
            # The URL's numeric id looked like a TMDb id but resolved to the
            # wrong film (hydrahd encodes its own ids in /movie/205708-watch-…).
            # Treat it as a miss so the search below -- which ends in the same
            # _title_matches guard -- gets a chance, instead of dropping the
            # item and leaving its old /find link forever.
            info = None
        if info is None:
            # A request post names no film, so there is nothing for TMDb to
            # match. Checked before the search, not after a miss, so these
            # never spend an API call or a cache slot.
            if title_text and is_request_post(title_text):
                stats["skipped"] += 1
                continue
            if title_text:
                # Candidate queries, most likely first: PTN's whole-title parse,
                # then the hand-written cleaner (which cuts a TV name at its
                # SxxEyy marker).
                search_titles = _search_title_candidates(title_text, is_tv=is_tv)
                # The release year narrows the search and comes from the URL
                # ("the-box-2026") or, for a Reddit item whose URL has none,
                # from the title itself. A wrong hint is recoverable: the
                # no-year search is retried below, which is what keeps a title
                # that *is* a year ("1917 2019 …") from being filtered out.
                year_hint = None
                url_year_match = URL_YEAR_RE.search(link_el.text)
                if url_year_match:
                    year_hint = url_year_match.group(1)
                if not year_hint:
                    year_hint = _title_year(title_text)
                searcher = search_tv if is_tv else search_movie
                info = None
                for search_title in search_titles:
                    info = searcher(search_title, year=year_hint) if search_title else None
                    if (not info or not info.poster_url) and year_hint:
                        info = searcher(search_title, year=None) if search_title else None
                    if info and info.poster_url:
                        break
                if not info or not info.poster_url:
                    # TMDb could not match any candidate. Search IMDb/OMDb
                    # for the release name, then resolve the film it names.
                    info = _fallback_lookup(
                        (search_titles[0] if search_titles else "") or title_text,
                        year_hint,
                        "tv" if is_tv else "movie",
                    )
                if not info or not info.poster_url:
                    stats["skipped"] += 1
                    continue
            else:
                stats["skipped"] += 1
                continue

        if info.title and title_text and not _title_matches(info.title, title_text):
            stats["skipped"] += 1
            continue

        # A TMDb *search* result carries no imdb_id (a details lookup does), so
        # fetch the details once to fill it -- the direct IMDb link depends on
        # it. Cache-backed, so the fetch is shared with every other item of the
        # same film or series.
        if info.tmdb_id and not info.imdb_id:
            media_kind = info.media_type or ("tv" if is_tv else "movie")
            details = movie_lookup(info.tmdb_id) if media_kind == "movie" else tv_lookup(info.tmdb_id)
            if details and details.imdb_id:
                info.imdb_id = details.imdb_id
            elif info.title:
                # TMDb's record holds no IMDb id: old entries keep an empty
                # string in external_ids, brand-new ones none at all. Ask IMDb's
                # index and re-enter TMDb via /find. The film it names must
                # still match the item title, or the /find search link is the
                # honest link and is kept (the id being wrong is worse than
                # none -- see atlantic-tv's "Monster" season).
                fb = _fallback_lookup(info.title, info.year, media_kind)
                if fb and fb.imdb_id and _title_matches(fb.title or "", title_text):
                    info.imdb_id = fb.imdb_id

        # The poster is normally TMDb's. When a resolved IMDb id's TMDb record
        # carries no poster at all (brand-new and obscure titles often have a
        # poster only on IMDb), OMDb's by-id record (?i=) supplies it. The id
        # entered this block only past the title guard, and the OMDb *record*
        # is checked again: a renamed film ("The Fix" -> "Hotel Tehran") must
        # not inherit the other title's poster.
        if info.imdb_id and not info.poster_url:
            omdb = _omdb_by_id(info.imdb_id)
            if omdb and omdb[3] and _title_matches(omdb[0], title_text or ""):
                info.poster_url = omdb[3]

        has_year = bool(HAS_YEAR_RE.search(title_text))
        has_bare_year = bool(HAS_BARE_YEAR_RE.search(title_text))

        # Replace messy title with clean TMDb title when available
        if not keep_titles and info.title and title_text and _title_matches(info.title, title_text) is not False:
            # Use TMDb's canonical title
            title_el.text = info.title
            changed = True

        if not keep_titles and info.year and not has_year and not has_bare_year and title_text and not HAS_YEAR_RE.search(title_el.text or ""):
            # Only add year if title doesn't already have it (after potential replacement)
            title_el.text = f"{title_el.text} ({info.year})"
            stats["years"] += 1
            changed = True

        # Re-attach the episode marker the source carried ("Series - 4x3",
        # "Show 2024 S02E03"). The canonical-title replacement above rewrote the
        # whole string to the TMDb series name, so every episode of a series
        # came out identical ("Reasonable Doubt (2022)") and the reader could
        # not tell one episode from another. TV feeds must keep the SxxEyy/NxM
        # marker; movies have none to keep.
        if not keep_titles and is_tv and title_text and title_el is not None:
            marker = EPISODE_TITLE_RE.search(title_text)
            if marker and not EPISODE_TITLE_RE.search(title_el.text or ""):
                title_el.text = f"{title_el.text} - {marker.group(0)}"
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
        want_imdb = not (
            _has_imdb_search_link(existing_desc or "")
            or _has_imdb_title_link(existing_desc or "")
        )

        # Build link block (player, IMDb, trailer) - always generate if needed.
        # The EpGuides link is *not* built here: `_attach_epguides_link` resolved
        # the real page above, and a title guessed into a slug ("Celebrity+Masterchef")
        # is a link to a page that does not exist.
        link_title = info.title or title_text
        link_block = ""
        parts = []
        # Add player link only if not already present
        try:
            existing_desc_check = desc_el.text if desc_el is not None else ""
            has_player = _has_player_anchor(existing_desc_check or "")
            tmdb_id = getattr(info, "tmdb_id", None)
            imdb_id = getattr(info, "imdb_id", None)
            if not has_player and (tmdb_id or imdb_id):
                page_link = build_player_page_link(
                    tmdb_id,
                    imdb_id,
                    getattr(info, "media_type", None),
                    link_title,
                    season=player_season,
                    episode=player_episode,
                    year=getattr(info, "year", None) or player_year,
                    epguides_slug=epguides_slug,
                )
                if page_link:
                    m = re.search(r'href="([^"]+)"', page_link)
                    if m:
                        parts.append(
                            f'<a href="{m.group(1)}" target="_blank" rel="noopener noreferrer">'
                            f'<b style="color:#007bff;">▶ Play</b></a>'
                        )
        except Exception:
            pass
        if link_title and want_imdb:
            if info.imdb_id:
                parts.append(_build_imdb_title_link(info.imdb_id))
            else:
                # Use current year if no year is present
                imdb_year = info.year if info.year else str(date.today().year)
                link_title_clean = YEAR_STRIP_RE.sub("", link_title).strip()
                parts.append(_build_imdb_link(link_title_clean, imdb_year))
        # The TMDb link is inserted below the IMDb anchor after the block is
        # written, not appended to `parts`: the anchor it must follow is not
        # always the one built here, so its position cannot be fixed yet.
        # `_has_tmdb_link` is the guard, so a re-run adds nothing.
        tmdb_link = ""
        if not _has_tmdb_link(existing_desc or ""):
            tmdb_id = getattr(info, "tmdb_id", None)
            media_type = getattr(info, "media_type", None) or ("tv" if is_tv else "movie")
            tmdb_link = _build_tmdb_link(tmdb_id, media_type, link_title)
        if link_title and want_trailer:
            link_title_clean = YEAR_STRIP_RE.sub("", link_title).strip()
            trailer_year = info.year
            if not trailer_year:
                m = re.search(r'\b(19\d{2}|20\d{2})\b', link_title)
                if m:
                    trailer_year = m.group(1)
            parts.append(_build_trailer_link(link_title_clean, trailer_year))
        if parts:
            link_block = "<br>" + "<br>".join(parts)
        # Don't append separate player links
        player_links = []

        # Player embed links are appended at the END of the description, not after
        # the poster. Each server is decided on its own guard tag, so a re-run
        # adds whatever is missing and leaves whatever is already there alone.
        # The standalone player-page link is prepended so it appears first —
        # users get the unified player experience by default.
        player_links: list[tuple[str, str]] = []
        if effective_servers:
            tmdb_id = getattr(info, "tmdb_id", None)
            imdb_id = getattr(info, "imdb_id", None)
            if tmdb_id or imdb_id:
                page_link = build_player_page_link(
                    tmdb_id,
                    imdb_id,
                    getattr(info, "media_type", None),
                    link_title,
                    season=player_season,
                    episode=player_episode,
                    year=getattr(info, "year", None) or player_year,
                    epguides_slug=epguides_slug,
                )
                if page_link:
                    player_links.append((page_link, "▶ Play</b>"))
            # Only show the Play link - skip individual server embed links
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
            # Torrent feeds get their player embed links at the very end.
            if player_links:
                appended = _append_player_links(targets, player_links)
                if appended:
                    stats["links"] += appended
                    changed = True
            stats["posters"] += 1
            if link_block or player_links:
                stats["links"] += 1
            changed = True
        else:
            # No poster replacement, but still insert links if needed.
            #
            # The poster is the item's identity and has to stay first: insert
            # the generated link block directly after the <img> the description
            # already carries. Prepending it instead pushed the poster below
            # Trailer/IMDb/EpGuides, so the item opened with links and closed
            # with the poster (the atlantic-tv / atlantic-movies order bug).
            if link_block:
                for el in targets:
                    if el.text:
                        img_m = IMG_TAG_RE.search(el.text)
                        if img_m:
                            el.text = (
                                el.text[: img_m.end()]
                                + link_block
                                + el.text[img_m.end():]
                            )
                        else:
                            el.text = link_block + "<br>" + el.text
                    else:
                        el.text = link_block
                stats["links"] += 1
                changed = True
            # Torrent feeds also get the player embed links at the very end.
            if player_links:
                appended = _append_player_links(targets, player_links)
                if appended:
                    stats["links"] += appended
                    changed = True

        # Place the TMDb link directly below the IMDb anchor, whichever run or
        # site wrote it. Done after the block above so a generated IMDb anchor
        # is already in the text; the site-provided case keeps IMDb above TMDb
        # instead of leaving TMDb in the block ahead of it.
        if tmdb_link:
            for el in targets:
                updated = _insert_after_imdb_anchor(el.text or "", tmdb_link)
                if updated != el.text:
                    el.text = updated
                    stats["links"] += 1
                    changed = True

        # An item enriched by the older block has TMDb above IMDb; move it
        # under. A no-op on items written by the branch above.
        for el in targets:
            if el.text:
                fixed = _move_tmdb_under_imdb(el.text)
                if fixed != el.text:
                    el.text = fixed
                    changed = True

        # Rewrite an IMDb search link to the direct title link now that the
        # IMDb id is known -- including links written before this existed,
        # which is the point of the upgrade. Adding a second IMDb link instead
        # would leave the item with two.
        if info.imdb_id:
            for el in (item.find(tag) for tag in DESC_TAGS):
                if el is not None and el.text:
                    upgraded = _upgrade_imdb_anchor(el.text, info.imdb_id)
                    if upgraded != el.text:
                        el.text = upgraded
                        changed = True

        # Same for the ▶ Play link: an item enriched before the episode params
        # existed keeps its link and gains the parameters, rather than being
        # skipped by `has_player` and keeping a series-level player forever.
        for el in (item.find(tag) for tag in DESC_TAGS):
            if el is not None and _has_player_anchor(el.text or ""):
                upgraded = _upgrade_player_anchor(
                    el.text,
                    season=player_season,
                    episode=player_episode,
                    year=getattr(info, "year", None) or player_year,
                    epguides_slug=epguides_slug,
                )
                if upgraded != el.text:
                    el.text = upgraded
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
