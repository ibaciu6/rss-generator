#!/usr/bin/env python3
"""Registry of streaming video-player embed servers.


.. versionchanged:: 2026-10-26
   Added build_player_page_link() — a unified "Play in Player" link
   prepended to every feed item. Points to ../player/index.html with
   tmdb/imdb/type/season/episode/title URL params.
Each server exposes a ``PlayerServer`` definition with the URL templates, the ID
type it accepts, the TV episode encoding style, and a guard tag used for
idempotent re-runs.  :func:`build_player_link` assembles an HTML anchor string
from a server id + metadata.

Servers that share a backend (e.g. VidNest ≈ VidLink) are listed separately so
that per-feed toggling works independently.

Supported / confirmed working (2026-10-24; extended 2026-10-26 via HAR analysis):
  cinesrc       cinesrc.st        — TMDB only, SxxEyy as ?s=&e= query params
  vidsrc        vidsrc.to         — IMDB preferred, TMDB fallback; S/E as path segments
  vidsrc_buzz   vidsrc.buzz       — TMDB only; S/E as path segments  (ads, multi-sub)
  vidlink       vidlink.pro       — TMDB only; S/E as path segments  (1080p, good for new titles)
  vembed        vsembed.ru        — TMDB only; S/E as path segments  (ads, reliable-ish)
  vidnest       vidnest.fun       — TMDB only; S/E as path segments  (ads) [NOTE: appears dead as of 2026-10, 404]
  vidfast       vidfast.vc        — TMDB only; S/E as path segments  (ads)
  2embed        2embed.cc         — TMDB only; S/E as path segments  (clean iframe player)
  medmedia03    medmedia03.wiki   — TMDB only; S/E as path segments  (numbered subdomains 1./2.)
  vidapi        vidapi.xyz        — IMDb only; S/E as path segments  (HAR-verified 2026-10-26)
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


def _season_episode(title: str) -> tuple[int, int] | None:
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


def build_player_page_link(
    tmdb_id: int | None,
    imdb_id: str | None,
    media_type: str | None,
    title: str | None,
    season: int | None = None,
    episode: int | None = None,
    year: str | None = None,
) -> str:
    """Build an HTML anchor that opens the standalone player page.

    The player page (player/index.html) renders all available server buttons;
    clicking any server button loads the chosen embed in the iframe.  The link
    is relative (``../player/index.html``) so it resolves correctly whether the
    feed is served from the feeds/ directory on GitHub Pages or a mirror.
    """
    if not tmdb_id and not imdb_id:
        return ""

    params: list[str] = []
    if tmdb_id:
        params.append(f"tmdb={tmdb_id}")
    if imdb_id:
        # Always use tt format for imdb
        clean_imdb = imdb_id if imdb_id.startswith("tt") else f"tt{imdb_id.lstrip('0').lstrip()}" if imdb_id.lstrip("0").isdigit() else imdb_id
        if not clean_imdb.startswith("tt") and imdb_id:
            clean_imdb = f"tt{imdb_id}"
        elif clean_imdb.startswith("tt"):
            pass
        else:
            clean_imdb = imdb_id
        # prefer tt format
        if imdb_id.startswith("tt"):
            clean_imdb = imdb_id
        elif imdb_id.isdigit():
            clean_imdb = f"tt{imdb_id}"
        else:
            clean_imdb = imdb_id
        params.append(f"imdb={clean_imdb}")
    if media_type:
        params.append(f"type={media_type}")
    if season is not None:
        params.append(f"season={season}")
    if episode is not None:
        params.append(f"episode={episode}")
    if year:
        params.append(f"year={year}")
    if title:
        params.append(f"title={quote(title)}")

    url = "https://ibaciu6.github.io/rss-generator/player/?" + "&".join(params)
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
      - the media type is ``tv`` but no season/episode marker is found in
        ``title`` *and* the server defines no series-level URL, or
      - ``server_id`` is not a known server.

    Idempotent: calling it on an already-enriched feed is a no-op (the guard tag
    in each ``PlayerServer`` is the check used by :func:`streaming_enricher._append_player_links`).
    """
    server = PLAYER_SERVERS.get(server_id)
    if not server:
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
        se = _season_episode(title)
        if se is None:
            # No episode marker: a season overview, not a single episode. Not
            # every server serves one at a stable URL, so fall back to the
            # dedicated template and skip the item entirely when it has none --
            # building /0/0 from the episode template would link to a phantom
            # episode.
            url = server.tv_series_url.format(imdb=ident, tmdb=ident) if server.tv_series_url else None
            if not url:
                return None
            label = server.label
        else:
            s, e = se
            url = server.tv_url.format(imdb=ident, tmdb=ident, season=s, episode=e)
            label = server.label
        return (
            f'<a href="{url}" target="_blank" '
            f'rel="noopener noreferrer"><b style="color:#6600cc;">{label}</b></a>'
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

    "medmedia03": PlayerServer(
        guard="MedMedia03</b>",
        label="MedMedia03",
        # Uses numbered subdomains 1. (movies) / 2. (tv); HAR shows both work for both types.
        movie_url="https://1.medmedia03.wiki/embed/movie/{tmdb}",
        tv_url="https://2.medmedia03.wiki/embed/tv/{tmdb}/{season}/{episode}",
        tv_series_url="https://1.medmedia03.wiki/embed/tv/{tmdb}",
        id_type="tmdb",
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
