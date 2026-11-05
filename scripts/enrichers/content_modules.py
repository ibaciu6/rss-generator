"""Independently enableable content modules for a feed item.

Each module owns one bit of an item's body (the air date, the poster, the play
link, the trailer, IMDb, TMDb, EpGuides) and declares where that bit belongs:
a **lead** module goes above the poster, the **poster** is the item's anchor,
and a **tail** module goes after the body with the other links.

A feed lists the modules it wants in ``config/sites.yaml``::

    content_modules: [air-date, poster, play]

Omitting the key falls back to the defaults for the feed's ``kind`` and
``category`` (see :func:`default_modules`). An explicit empty list disables
every module for that feed.

The composer is a placement pass, not a generator. The streaming enricher writes
each fragment once; this pass claims it back from wherever the source put it and
lays the enabled modules out in one canonical order. Disabling a module never
deletes content: it only stops that bit from being generated and placed. That
matters for article feeds, where the first image is an illustration and the
site's own links belong to the prose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Literal

ModuleSlot = Literal["lead", "poster", "tail"]

# Categories whose feeds are enriched from TMDb and carry a poster/links block.
STREAMING_CATEGORIES = frozenset(
    {"movies", "episodes", "series", "cinema", "torrents", "releases"}
)
# Categories whose items are downloads, so a ▶ Play link is useful by default.
PLAY_CATEGORIES = frozenset({"torrents", "releases"})
# Categories whose items are episodes, so an air date is useful by default.
EPISODE_CATEGORIES = frozenset({"series", "episodes"})


def _player_labels() -> tuple[str, ...]:
    """Every label the enricher has ever written for a play link.

    The player-page link is labelled ``▶ Play`` now; older items can still carry
    one of the per-server anchors (``CineSrc``, ``VidSrc``, …). They are all the
    same module, so the composer has to recognise every published spelling.
    """
    labels = ["▶ Play"]
    try:
        from scripts.enrichers.player_servers import PLAYER_SERVERS
    except ImportError:  # pragma: no cover - direct module use without the package
        return tuple(labels)
    labels.extend(server.guard.removesuffix("</b>") for server in PLAYER_SERVERS.values())
    return tuple(dict.fromkeys(labels))


@dataclass(frozen=True)
class ContentModule:
    """One independently enableable piece of an item body."""

    name: str
    slot: ModuleSlot
    #: Anchor labels this module claims back from the body, if it is a tail bit.
    labels: tuple[str, ...] = ()
    #: ``kind`` values that enable the module by default.
    default_kinds: tuple[str, ...] = ()
    #: ``category`` values that enable the module by default.
    default_categories: tuple[str, ...] = ()
    #: Whether a streaming feed (category/enhance_mode) enables it by default.
    default_streaming: bool = False


# Registry order is the layout order: a lead module first, the poster next, then
# tail modules in the order their links should read.
CONTENT_MODULES: tuple[ContentModule, ...] = (
    ContentModule(
        name="air-date",
        slot="lead",
        default_kinds=("series",),
        default_categories=tuple(EPISODE_CATEGORIES),
        default_streaming=True,
    ),
    ContentModule(name="poster", slot="poster", default_streaming=True),
    ContentModule(
        name="play",
        slot="tail",
        labels=_player_labels(),
        default_categories=tuple(PLAY_CATEGORIES),
    ),
    ContentModule(name="trailer", slot="tail", labels=("Trailer",), default_streaming=True),
    ContentModule(name="imdb", slot="tail", labels=("IMDb",), default_streaming=True),
    ContentModule(name="tmdb", slot="tail", labels=("TMDb",), default_streaming=True),
    ContentModule(name="epguides", slot="tail", labels=("EpGuides",), default_streaming=True),
)

MODULE_BY_NAME: dict[str, ContentModule] = {m.name: m for m in CONTENT_MODULES}
MODULE_NAMES: tuple[str, ...] = tuple(MODULE_BY_NAME)


def known_modules() -> list[str]:
    """Names accepted by ``content_modules:`` (for config validation)."""
    return list(MODULE_NAMES)


def _ordered(names: set[str] | list[str]) -> tuple[str, ...]:
    """Return `names` in registry order, dropping unknown ones."""
    wanted = set(names)
    return tuple(m.name for m in CONTENT_MODULES if m.name in wanted)


def default_modules(
    *,
    kind: str | None = None,
    category: str | None = None,
    enhance_mode: str | None = None,
    player_servers: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """The modules a feed gets when it does not configure ``content_modules``.

    The defaults follow the feed's identity rather than a per-feed list: series
    and streaming feeds get the poster/links block and the air date, download
    feeds also get the play link, and article feeds (news, blogs, cyber, …) get
    nothing. A feed can always override the result with an explicit list.
    """
    if (enhance_mode or "").strip().lower() == "none":
        return ()

    category = (category or "").strip().lower()
    streaming = (
        (enhance_mode or "").strip().lower() == "streaming"
        or category in STREAMING_CATEGORIES
    )

    enabled: list[str] = []
    if streaming or kind == "series" or category in EPISODE_CATEGORIES:
        enabled.append("air-date")
    if streaming:
        enabled.append("poster")

    # An explicit list, including an empty one, wins over the category default.
    play = (
        bool(player_servers)
        if player_servers is not None
        else category in PLAY_CATEGORIES
    )
    if play:
        enabled.append("play")

    if streaming:
        enabled.extend(("trailer", "imdb", "tmdb", "epguides"))

    return _ordered(enabled)


def resolve_modules(
    configured: tuple[str, ...] | list[str] | None,
    *,
    kind: str | None = None,
    category: str | None = None,
    enhance_mode: str | None = None,
    player_servers: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Resolve a feed's configured module list, or its defaults when absent.

    ``None`` means "not configured"; ``()`` means "configured as empty, so run
    nothing". An explicit list keeps the caller's tail order (so a site can put
    Trailer before ▶ Play) while lead/poster modules stay in their fixed slots.
    """
    if configured is None:
        return default_modules(
            kind=kind,
            category=category,
            enhance_mode=enhance_mode,
            player_servers=player_servers,
        )
    # Validate names here as well as in core.config so a direct caller fails
    # loudly instead of silently dropping a typo.
    unknown = [name for name in configured if name not in MODULE_BY_NAME]
    if unknown:
        raise ValueError(
            f"unknown content module(s) {sorted(unknown)}; known: {sorted(MODULE_BY_NAME)}"
        )
    # Keep the caller's relative order for tail modules, but drop duplicates.
    return tuple(dict.fromkeys(str(name).strip() for name in configured))


def module_enabled(modules: tuple[str, ...] | None, name: str) -> bool:
    """Whether `name` runs for this feed.

    ``None`` means "no module list supplied" (a direct caller that predates the
    registry); treat that as every module enabled, which is the old behaviour.
    """
    return modules is None or name in modules


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

_IMG_TAG_RE = re.compile(r"<img\s[^>]*>", re.IGNORECASE)
_ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.IGNORECASE | re.DOTALL)
_FEED_DAY_RE = re.compile(
    r'(?:<br\s*/?>\s*)?<p id="feed-day">.*?</p>', re.IGNORECASE | re.DOTALL
)
_EDGE_BR_RE = re.compile(
    r"^(?:\s*<br\s*/?>\s*)+|(?:\s*<br\s*/?>\s*)+$", re.IGNORECASE
)
_BR_RUN_RE = re.compile(r"(?:\s*<br\s*/?>\s*){2,}", re.IGNORECASE)

# Remove a stray one-word episode title only in the narrow generated-feed shape
# where it appears between a break and the break that starts the body paragraph.
# The negative lookahead protects the conventional "Body" label.
_EPISODE_NAME_RE = re.compile(
    r"<br\s*/?>\s*(?!Body\b)[^\s<]+?(?=<br\s*/?>\s*(?:<p\b|$))", re.IGNORECASE
)
_LEADING_BR_RE = re.compile(r"^(?:\s*<br\s*/?>\s*)+", re.IGNORECASE)


def _anchor_text(anchor: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", anchor)).strip()


def air_date_html(value: str | None) -> str:
    """The generated air-date paragraph for a date string, or "" without one.

    Accepts the RFC 2822 spelling a feed's ``<pubDate>`` uses and an ISO date,
    because a legacy item may carry either. Only the date part is published.
    """
    if not value:
        return ""
    text = value.strip()
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    return (
        f'<p id="feed-day"><strong>Air date:</strong> '
        f"{parsed.strftime('%Y-%m-%d')}</p>"
    )


def _clean_body(text: str) -> str:
    """Drop the breaks left behind when fragments were lifted out of the body.

    Removing the poster, the date and a run of links leaves the `<br>` junctions
    that separated them. Collapsing runs is deterministic, so re-running the
    composer cannot add one more break per pass (the bug that made the air-date
    pass grow a `<br>` on every run of a poster feed).
    """
    text = _EDGE_BR_RE.sub("", text.strip())
    text = _BR_RUN_RE.sub("<br>", text)
    return text.strip()


def compose_description(
    desc: str,
    modules: tuple[str, ...] | None,
    *,
    pub_date: str | None = None,
) -> str:
    """Lay an item's enabled content modules out in their canonical order.

    ``modules`` is the resolved list for the feed. ``None`` leaves the
    description untouched: an article feed, or a caller that predates the
    registry, must not have its prose reordered. An empty tuple is also a
    no-op -- with no modules there is no layout to impose.
    """
    if not desc or not modules:
        return desc

    enabled = [name for name in modules if name in MODULE_BY_NAME]
    if not enabled:
        return desc

    spans: list[tuple[int, int]] = []
    poster = ""
    date_paragraph = ""

    if module_enabled(modules, "poster"):
        img_match = _IMG_TAG_RE.search(desc)
        if img_match:
            poster = img_match.group(0)
            spans.append((img_match.start(), img_match.end()))

    if module_enabled(modules, "air-date"):
        date_match = _FEED_DAY_RE.search(desc)
        if date_match:
            # The leading `<br>` belongs to the paragraph's slot, not the
            # paragraph: keeping it would put a stray break above the date every
            # time the composer lifts the date to the top.
            date_paragraph = _LEADING_BR_RE.sub("", date_match.group(0))
            spans.append((date_match.start(), date_match.end()))
        else:
            date_paragraph = air_date_html(pub_date)
    else:
        # The air date is the pipeline's own paragraph, not the source's text, so
        # disabling the module also retires the copy a previous run generated.
        # Site prose is never matched here: the marker id is ours.
        for date_match in _FEED_DAY_RE.finditer(desc):
            spans.append((date_match.start(), date_match.end()))

    links: list[str] = []
    claimed_labels: set[str] = set()
    for name in enabled:
        module = MODULE_BY_NAME[name]
        if module.slot != "tail":
            continue
        # A module that follows one with the same label (none today) would have
        # nothing left to claim; the guard keeps that honest.
        labels = set(module.labels) - claimed_labels
        claimed = False
        for match in _ANCHOR_RE.finditer(desc):
            if _anchor_text(match.group(0)) in labels:
                links.append(match.group(0))
                spans.append((match.start(), match.end()))
                claimed = True
        if claimed:
            claimed_labels.update(labels)

    body = desc
    for start, end in sorted(spans, key=lambda span: span[0], reverse=True):
        body = body[:start] + body[end:]
    # Remove the isolated episode-name fragment without touching HTML body copy.
    body = _EPISODE_NAME_RE.sub("", body)
    body = _clean_body(body)

    pieces: list[str] = []
    if date_paragraph:
        pieces.append(date_paragraph)
    if poster:
        pieces.append(poster)

    if pieces:
        out = "<br>".join(pieces)
        if body:
            out += "<br>" + body
        if links:
            out += "<br>" + "<br>".join(links)
        return out

    # No lead/poster module claimed anything. The body may still carry a poster
    # that a disabled poster module must not delete; it is left exactly where it
    # was, and only the enabled tail links are moved to the end.
    out = body
    if links:
        out = (out + "<br>" if out else "") + "<br>".join(links)
    return out


def strip_air_date(desc: str) -> str:
    """Remove the generated air-date paragraph (used by feeds that disable it)."""
    return _FEED_DAY_RE.sub("", desc or "").strip()
