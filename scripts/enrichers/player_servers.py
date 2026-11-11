#!/usr/bin/env python3
"""Registry of streaming video-player embed servers.


.. versionchanged:: 2026-10-26
   Added build_player_page_link() — a unified "Play in Player" link
   prepended to every feed item. Points to ../player/index.html with
   tmdb/imdb/type/season/episode/epguides/title URL params, the year folded
   into the title rather than sent as its own parameter.
Each server exposes a ``PlayerServer`` definition with the URL templates, the ID
type it accepts, the TV episode encoding style, and a guard tag used for
idempotent re-runs.  :func:`build_player_link` assembles an HTML anchor string
from a server id + metadata.

Servers that share a backend (e.g. VidNest ≈ VidLink) are listed separately so
that per-feed toggling works independently.

Supported / confirmed working (2026-10-24; extended 2026-10-26 via HAR
analysis; popup sweep 2026-11-11 — every server but medmedia03 and cdnm.ink
opens popups; vidspark and cdnm.ink added 2026-11-11 from a framed-by-player
probe — only servers that play inside the player page's iframe qualify):
  cinesrc       cinesrc.st        — TMDB only, SxxEyy as ?s=&e= query params  (ads)
  vidsrc        vidsrc.to         — IMDB preferred, TMDB fallback; S/E as path segments  (ads)
  vidsrc_buzz   vidsrc.buzz       — TMDB only; S/E as path segments  (ads, multi-sub)
  vidlink       vidlink.pro       — TMDB only; S/E as path segments  (ads; 1080p, good for new titles)
  vembed        vsembed.ru        — TMDB only; S/E as path segments  (ads, reliable-ish)
  vidnest       vidnest.fun       — TMDB only; S/E as path segments  (ads) [NOTE: appears dead as of 2026-10, 404]
  vidfast       vidfast.vc        — TMDB only; S/E as path segments  (ads)
  2embed        2embed.cc         — TMDB only; S/E as path segments  (ads)
  vidspark      vidspark.to       — TMDB only; S/E as path segments; episode-only TV, no series page  (ads — one popup per play)
  medmedia03    medmedia03.wiki   — TMDB only; S/E as path segments  (popup-free; numbered subdomains 1./2.)
  cdnm          share.cdnm.ink    — IMDb only, and movies only: no TV embed exists  (popup-free)
  vidapi        vidapi.xyz        — IMDb only; S/E as path segments  (ads; HAR-verified 2026-10-26)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

# ID types a server accepts
IdType = Literal["tmdb", "imdb", "imdb_or_tmdb"]


@dataclass(frozen=True)
class PlayerServer:
    """Definition for one streaming embed server."""

    #: Guard tag written into the HTML anchor label, e.g. ``"VidLink</b>"``.
    #: Re-runs check for this string to avoid adding a duplicate link.
    guard: str

    #: Short human-readable name used in the rendered anchor.
    label: str

    #: Movie embed URL. Use ``{imdb}`` or ``{tmdb}`` as placeholders.
    movie_url: str

    #: TV embed URL. Use ``{imdb}``, ``{tmdb}``, ``{season}``, ``{episode}`` as placeholders.
    tv_url: str

    #: Series-level embed URL, used when a TV item carries no ``S02E03``/``4x3``
    #: marker. Use ``{imdb}`` or ``{tmdb}`` only (no season/episode). Left empty
    #: when a server has no usable series page.
    tv_series_url: str = ""

    #: What kind of ID the server needs.
    id_type: IdType = "imdb_or_tmdb"

    #: True when the server resolves only real IMDb ids — the ``tt<tmdb>``
    #: fallback is a phantom id there, so items without an IMDb id get no
    #: link at all rather than one that cannot play.
    needs_imdb: bool = False


def season_episode(title: str) -> tuple[int, int] | None:
    """Extract (season, episode) from a title string.

    Tries both ``S02E03`` and ``4x3`` forms.  Returns ``None`` when nothing
    matches (item is a movie or a season overview).
    """
    m = re.search(r"S(\d{1,2})\s*E(\d{1,2})", title, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\b(\d{1,2})x(\d{1,2})\b", title, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


_TMDB_RE = re.compile(r"^\d+$")


def _to_imdb_id(ident: str) -> str:
    """Coerce `ident` to a VidSrc.to-compatible IMDB id.

    VidSrc.to's embed URLs want a ``tt``-prefixed IMDB id. When the enricher
    only has a TMDb id, the fallback is a bare number like ``136927``, which
    VidSrc.to does not accept -- prefix it with ``tt`` and let the site map the
    TMDb id to the real IMDB id itself. Anything that already looks like an IMDB
    id (``tt14452776``), or is neither a plain number nor empty, passes through
    untouched.
    """
    if ident.startswith("tt") or not _TMDB_RE.match(ident):
        return ident
    return f"tt{ident}"


# Guard for the standalone player-page link, prepended to every feed item.
_PLAYER_PAGE_LINK_GUARD = "▶ Play</b>"

# The player page is served from this repository's own project site: the Pages
# deploy publishes `player/index.html` at this path. The page reads nothing but
# its query string, so the origin makes no difference to how it works.
PLAYER_PAGE_BASE_URL = "https://ibaciu6.github.io/rss-generator/player/"

# Base URLs that were published before the player returned to this project site.
# The in-place upgrade has to match them: an old anchor the rewrite cannot see is
# an anchor the writer treats as missing, and the item ends up with two ▶ Play
# links, every run, in every reader.
LEGACY_PLAYER_PAGE_BASE_URLS = ("https://ibaciu6.github.io/player/",)

# A title already carrying a release year needs no folded one, whichever
# spelling the feed used — "(2026)", "[2026]" or a bare trailing 2026.
_HAS_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def build_player_page_url(
    tmdb_id: int | str | None,
    imdb_id: str | None,
    media_type: str | None,
    title: str | None,
    season: int | None = None,
    episode: int | None = None,
    year: str | None = None,
    epguides_slug: str | None = None,
) -> str:
    """The player page URL for this item, or "" when no id is known.

    Parameter order is the published contract, and every value is quoted: the
    ids and the type arrive from a scraped page or a third-party API, and the
    one caller that rebuilds an existing href
    (:func:`streaming_enricher._player_page_href`) feeds them back through here
    after percent-decoding, so an href that was never well-formed cannot put
    markup into a description.
    """
    if not tmdb_id and not imdb_id:
        return ""

    params: list[str] = []
    if tmdb_id:
        params.append(f"tmdb={quote(str(tmdb_id), safe='')}")
    if imdb_id:
        params.append(f"imdb={quote(_to_imdb_id(str(imdb_id)), safe='')}")
    if media_type:
        params.append(f"type={quote(str(media_type), safe='')}")
    if season is not None and episode is not None:
        # A pair, like the player's own episode check: a season with no episode
        # is a series overview, and half a marker would build an href the
        # upgrade path disagrees with.
        params.append(f"season={season}")
        params.append(f"episode={episode}")
    if epguides_slug:
        params.append(f"epguides={quote(str(epguides_slug), safe='')}")
    if title:
        display = f"{title} ({year})" if year and not _HAS_YEAR_RE.search(title) else title
        params.append(f"title={quote(display, safe='')}")

    return PLAYER_PAGE_BASE_URL + "?" + "&".join(params)


def build_player_page_link(
    tmdb_id: int | None,
    imdb_id: str | None,
    media_type: str | None,
    title: str | None,
    season: int | None = None,
    episode: int | None = None,
    year: str | None = None,
    epguides_slug: str | None = None,
) -> str:
    """Build an HTML anchor that opens the standalone player page.

    The player page (player/index.html) renders all available server buttons;
    clicking any server button loads the chosen embed in the iframe.  The link
    carries ``tmdb``/``imdb``/``type``, the episode ``season``/``episode`` for a
    marked TV item, the resolved ``epguides`` slug when one is known, and the
    ``title`` with the year folded in ("Best Medicine (2026)") — the player uses
    that string for its header and its trailer search, and a bare ``year``
    parameter would be ignored.

    :func:`streaming_enricher._player_page_href` rewrites published links
    through the same :func:`build_player_page_url`, so the fresh-write and the
    upgrade paths cannot drift apart.
    """
    url = build_player_page_url(
        tmdb_id,
        imdb_id,
        media_type,
        title,
        season=season,
        episode=episode,
        year=year,
        epguides_slug=epguides_slug,
    )
    if not url:
        return ""

    return (
        f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
        f'<b style="color:#007bff;">{_PLAYER_PAGE_LINK_GUARD}</a>'
    )


def build_player_link(
    server_id: str,
    tmdb_id: int | None,
    imdb_id: str | None,
    media_type: str | None,
    title: str = "",
) -> str | None:
    """Build an HTML anchor for ``server_id`` or return None when it can't be built.

    ``server_id`` must be a key in ``PLAYER_SERVERS``.  Returns None when:
      - the required ID is not available for this server's ``id_type``,
      - the server is marked ``needs_imdb`` and the item has no IMDb id,
      - the media type is ``tv`` but no season/episode marker is found in
        ``title`` *and* the server defines no series-level URL,
      - the media type is ``tv`` and the server has no TV embed at all
        (cdnm.ink serves movies only), or
      - ``server_id`` is not a known server.

    Idempotent: calling it on an already-enriched feed is a no-op (the guard tag
    in each ``PlayerServer`` is the check used by :func:`streaming_enricher._append_player_links`).
    """
    server = PLAYER_SERVERS.get(server_id)
    if not server:
        return None

    # A server that maps only real IMDb ids (cdnm.ink) gets nothing from the
    # tt-prefixed TMDb fallback below — a bare number wearing the prefix is a
    # phantom id there — so an item without an IMDb id is skipped rather than
    # given a link that cannot play.
    if server.needs_imdb and not imdb_id:
        return None

    # Resolve which ID to use
    if server.id_type in ("imdb_or_tmdb", "imdb"):
        raw = imdb_id or (str(tmdb_id) if tmdb_id else None)
    else:  # tmdb only
        raw = str(tmdb_id) if tmdb_id else None

    if not raw:
        return None
    # VidSrc.to and VidAPI want IMDB ids (tt-prefixed). Anything that already
    # looks like an IMDB id passes through untouched; bare TMDb numbers get
    # the "tt" prefix prepended so the embed site can look up the IMDB id itself.
    ident = _to_imdb_id(raw) if server.id_type in ("imdb_or_tmdb", "imdb") else raw

    if media_type == "tv":
        se = season_episode(title)
        if se is None:
            # No episode marker: a season overview, not a single episode. Not
            # every server serves one at a stable URL, so fall back to the
            # dedicated template and skip the item entirely when it has none --
            # building /0/0 from the episode template would link to a phantom
            # episode.
            url = server.tv_series_url.format(imdb=ident, tmdb=ident) if server.tv_series_url else None
        else:
            s, e = se
            # The episode template can be missing too — cdnm.ink has no TV
            # embed at all — and formatting an empty one would publish an
            # anchor with an empty href.
            url = server.tv_url.format(imdb=ident, tmdb=ident, season=s, episode=e) if server.tv_url else None
        if not url:
            return None
        return (
            f'<a href="{url}" target="_blank" '
            f'rel="noopener noreferrer"><b style="color:#6600cc;">{server.label}</b></a>'
        )

    # Movie
    url = server.movie_url.format(imdb=ident, tmdb=ident)
    return (
        f'<a href="{url}" target="_blank" '
        f'rel="noopener noreferrer"><b style="color:#6600cc;">{server.label}</b></a>'
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PLAYER_SERVERS: dict[str, PlayerServer] = {
    # ------------------------------------------------------------------
    # Confirmed working (tested 2026-10-23)
    # ------------------------------------------------------------------

    "cinesrc": PlayerServer(
        guard="CineSrc</b>",
        label="CineSrc",
        movie_url="https://cinesrc.st/embed/movie/{tmdb}",
        tv_url="https://cinesrc.st/embed/tv/{tmdb}?s={season}&e={episode}",
        tv_series_url="https://cinesrc.st/embed/tv/{tmdb}",
        id_type="tmdb",
    ),

    "vidsrc": PlayerServer(
        guard="VidSrc</b>",
        label="VidSrc",
        movie_url="https://vidsrc.to/embed/movie/{imdb}",
        tv_url="https://vidsrc.to/embed/tv/{imdb}/{season}/{episode}",
        tv_series_url="https://vidsrc.to/embed/tv/{imdb}",
        id_type="imdb_or_tmdb",
    ),

    "vidsrc_buzz": PlayerServer(
        guard="VidSrc.buzz</b>",
        label="VidSrc.buzz",
        movie_url="https://vidsrc.buzz/embed/movie/{tmdb}?autoplay=true",
        tv_url="https://vidsrc.buzz/embed/tv/{tmdb}/{season}/{episode}?autoplay=true",
        id_type="tmdb",
    ),

    "vidlink": PlayerServer(
        guard="VidLink</b>",
        label="VidLink",
        movie_url="https://vidlink.pro/movie/{tmdb}?autoplay=true",
        tv_url="https://vidlink.pro/tv/{tmdb}/{season}/{episode}?autoplay=true",
        id_type="tmdb",
    ),

    "vembed": PlayerServer(
        guard="VEmbed</b>",
        label="VEmbed",
        movie_url="https://vsembed.ru/embed/movie/{tmdb}?autoplay=1",
        tv_url="https://vsembed.ru/embed/tv/{tmdb}?s={season}&e={episode}&autoplay=1",
        id_type="tmdb",
    ),

    "vidnest": PlayerServer(
        guard="VidNest</b>",
        label="VidNest",
        movie_url="https://vidnest.fun/movie/{tmdb}?autoplay=1",
        tv_url="https://vidnest.fun/tv/{tmdb}/{season}/{episode}?autoplay=1",
        id_type="tmdb",
    ),

    "vidfast": PlayerServer(
        guard="VidFast</b>",
        label="VidFast",
        movie_url="https://vidfast.vc/movie/{tmdb}?autoPlay=true",
        tv_url="https://vidfast.vc/tv/{tmdb}/{season}/{episode}?autoPlay=true",
        id_type="tmdb",
    ),
    "2embed": PlayerServer(
        guard="2Embed</b>",
        label="2Embed",
        movie_url="https://2embed.cc/embed/movie/{tmdb}",
        tv_url="https://2embed.cc/embed/tv/{tmdb}/{season}/{episode}",
        tv_series_url="https://2embed.cc/embed/tv/{tmdb}",
        id_type="tmdb",
    ),

    "vidspark": PlayerServer(
        guard="VidSpark</b>",
        label="VidSpark",
        # The vidora family's own host (moviesapi.to and cdn.vidspark.to both
        # land here). No series page: the TV route is episode-only. Opens one
        # popup per play interaction (framed probe, 2026-11-11).
        movie_url="https://vidspark.to/movie/{tmdb}",
        tv_url="https://vidspark.to/tv/{tmdb}/{season}/{episode}",
        id_type="tmdb",
    ),

    "medmedia03": PlayerServer(
        guard="MedMedia03</b>",
        label="MedMedia03",
        # Uses numbered subdomains 1. (movies) / 2. (tv); HAR shows both work for both types.
        movie_url="https://1.medmedia03.wiki/embed/movie/{tmdb}",
        tv_url="https://2.medmedia03.wiki/embed/tv/{tmdb}/{season}/{episode}",
        tv_series_url="https://1.medmedia03.wiki/embed/tv/{tmdb}",
        id_type="tmdb",
    ),

    "cdnm": PlayerServer(
        guard="cdnm.ink</b>",
        label="cdnm.ink",
        # The embed backend the Romanian feed sites frame (HAR, 2026-11-11):
        # share.cdnm.ink wraps a cdnmovies-stream.online player and serves the
        # m3u8 from s1.cdnmvs.online. Movies only — no TV shape was ever
        # captured — and keyed by a real IMDb id: the /embed/tmdb/ route
        # answers 200 but plays nothing, so the tt<tmdb> fallback is a phantom
        # and items without an IMDb id get no link. Popup-free.
        movie_url="https://share.cdnm.ink/embed/imdb/{imdb}",
        tv_url="",
        id_type="imdb",
        needs_imdb=True,
    ),

    "vidapi": PlayerServer(
        guard="VidAPI</b>",
        label="VidAPI",
        # IMDb IDs only — HAR shows imdb ID format in embed URLs.
        movie_url="https://vidapi.xyz/embed/movie/{imdb}",
        tv_url="https://vidapi.xyz/embed/tv/{imdb}/{season}/{episode}",
        tv_series_url="https://vidapi.xyz/embed/tv/{imdb}",
        id_type="imdb",
    ),

}


# Default server list for torrent/release feeds when sites.yaml omits the field.
# Ordered by reliability: CineSrc + VidSrc Pro are most stable; VidLink is the
# best fallback for newer releases; the rest have ads but are verified working.
DEFAULT_TORRENT_SERVERS = ("cinesrc", "vidsrc", "vidlink", "2embed", "medmedia03", "vidapi")
