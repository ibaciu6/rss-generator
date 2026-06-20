from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

from core.logging_utils import get_logger
from scraper.parser import ParsedItem


logger = get_logger(__name__)

# Downscale any TMDB poster path segment to a reader-friendly width.
TMDB_SIZE_PATTERN = re.compile(
    r"(https://image\.tmdb\.org/t/p/)(?:w\d+|original)(/)"
)
TMDB_REPLACEMENT_SIZE = r"\1w342\2"

# Enforced on every feed item description (HTML scrapes, RSS/WordPress fallbacks).
# Sources deliver posters at 183..342 px wide; using ``max-width`` alone let the
# smaller ones render at native size, so posters looked bigger on some feeds
# than on others. Pin width to a fixed value (both as CSS and as the ``width``
# HTML attribute for readers that strip styles) so every card is the same size
# regardless of the source image resolution.
POSTER_IMG_WIDTH = 300
POSTER_IMG_MAX_HEIGHT = 450
POSTER_IMG_STYLE = (
    f"width:{POSTER_IMG_WIDTH}px;height:auto;"
    f"max-height:{POSTER_IMG_MAX_HEIGHT}px;"
    "object-fit:contain;display:block;border-radius:4px;"
)

FAILURE_TITLE_SUFFIX = " (unavailable)"
# Cap for failure-reason text baked into the placeholder feed. Raw Playwright
# call logs can be multi-kilobyte and are noise for RSS readers.
FAILURE_REASON_MAX_CHARS = 800
# Hint for aggregators (e.g. Inoreader ~hourly polls; min interval ~30 min per
# https://www.inoreader.com/feed-fetcher ). WebSub further reduces their polls.
FEED_TTL_MINUTES = 60
FEED_UPDATE_PERIOD = "hourly"
FEED_UPDATE_FREQUENCY = "1"
WEBSUB_HUB_URL = "https://pubsubhubbub.appspot.com/"
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
SYNDICATION_NS = "http://purl.org/rss/1.0/modules/syndication/"

ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("sy", SYNDICATION_NS)


def _feed_self_link_href(output_path: Path) -> str:
    """
    RSS/Atom self link. Relative filename works locally; set RSS_FEED_PUBLIC_BASE
    in CI (e.g. GitHub Pages root) so readers like Inoreader get an absolute URI.
    """
    base = os.environ.get("RSS_FEED_PUBLIC_BASE", "").strip().rstrip("/")
    if not base:
        return output_path.name
    try:
        resolved_out = output_path.resolve()
        resolved_cwd = Path.cwd().resolve()
        rel = resolved_out.relative_to(resolved_cwd)
    except ValueError:
        rel = Path(output_path.name)
    return f"{base}/{rel.as_posix()}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_description_html(description: str) -> str:
    """
    Apply TMDB downsizing, then force poster <img> bounds so readers never get
    full-resolution posters (matches config/sites.yaml intent for all sources).
    """
    text = TMDB_SIZE_PATTERN.sub(TMDB_REPLACEMENT_SIZE, description)
    if "<img" not in text.lower():
        return text
    soup = BeautifulSoup(f"<div>{text}</div>", "html.parser")
    wrapper = soup.find("div")
    if wrapper is None:
        return text
    for img in wrapper.find_all("img"):
        # Drop any height carried over from the source markup so the fixed
        # width + aspect ratio decide the rendered height. Keep (or set) an
        # explicit ``width`` attribute so RSS readers that strip CSS still
        # render every poster at the same column width.
        img.attrs.pop("height", None)
        img["width"] = str(POSTER_IMG_WIDTH)
        img["style"] = POSTER_IMG_STYLE
    return wrapper.decode_contents()


def generate_rss(
    items: Iterable[ParsedItem],
    site_name: str,
    site_url: str,
    category: Optional[str],
    output_path: Path,
) -> None:
    """
    Generate RSS 2.0 feed into `feeds/`.
    Relative item links are resolved against site_url so readers get absolute URLs.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = _now_utc()

    fg = _build_feed(
        feed_title=site_name,
        site_url=site_url,
        output_path=output_path,
        description=f"Feed generated for {site_name}",
        generated_at=generated_at,
    )

    for item in items:
        absolute_link = urljoin(site_url, item.link)
        fe = fg.add_entry()
        fe.id(absolute_link)
        fe.title(item.title)
        fe.link(href=absolute_link)
        if item.description:
            desc = _normalize_description_html(item.description)
            fe.description(desc)
            fe.content(desc, type="html")
        if item.pub_date:
            published_at = _ensure_timezone(item.pub_date)
            fe.pubDate(published_at)
            fe.updated(published_at)
        if category:
            fe.category(term=category)

    _write_feed(
        fg,
        output_path,
        site_name,
        site_url=site_url,
        generated_at=generated_at,
        failure=False,
    )


def _sanitize_failure_reason(error_message: str) -> str:
    """Strip Playwright "Call log:" stacks and collapse whitespace so the
    published feed description stays under a sane size for readers."""

    lines = []
    for raw_line in str(error_message).splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        # Playwright "Call log: ... - navigating to ..." adds no signal.
        if stripped.startswith("Call log:") or stripped.startswith("- "):
            continue
        lines.append(line.strip())
    cleaned = " ".join(lines) or str(error_message).strip() or "unknown error"
    if len(cleaned) > FAILURE_REASON_MAX_CHARS:
        cleaned = cleaned[: FAILURE_REASON_MAX_CHARS - 1].rstrip() + "…"
    return cleaned


def generate_failure_rss(
    site_name: str,
    site_url: str,
    output_path: Path,
    error_message: str,
) -> None:
    """
    Generate a valid RSS feed that explains why the source is currently unavailable.
    """
    failed_at = _now_utc()
    reason = _sanitize_failure_reason(error_message)
    description = (
        f"Last generation attempt failed on {failed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}. "
        f"Reason: {reason}"
    )
    fg = _build_feed(
        feed_title=f"{site_name}{FAILURE_TITLE_SUFFIX}",
        site_url=site_url,
        output_path=output_path,
        description=description,
        generated_at=failed_at,
    )

    entry = fg.add_entry()
    entry.id(f"{site_url}#generation-status")
    entry.title("Feed generation failed")
    entry.link(href=site_url)
    entry.description(description)
    entry.content(f"<p>{escape(description)}</p>", type="html")
    entry.pubDate(failed_at)
    entry.updated(failed_at)

    _write_feed(
        fg,
        output_path,
        site_name,
        site_url=site_url,
        generated_at=failed_at,
        failure=True,
    )


def is_failure_feed_title(title: Optional[str]) -> bool:
    return bool(title and title.endswith(FAILURE_TITLE_SUFFIX))


def _build_feed(
    feed_title: str,
    site_url: str,
    output_path: Path,
    description: str,
    generated_at: datetime,
) -> FeedGenerator:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fg = FeedGenerator()
    fg.id(site_url)
    fg.title(feed_title)
    fg.link(href=site_url, rel="alternate")
    fg.link(href=_feed_self_link_href(output_path), rel="self")
    fg.description(description)
    fg.language("en")
    fg.pubDate(generated_at)
    fg.updated(generated_at)
    fg.ttl(str(FEED_TTL_MINUTES))
    return fg


def _write_feed(
    fg: FeedGenerator,
    output_path: Path,
    site_name: str,
    site_url: str,
    generated_at: datetime,
    failure: bool,
) -> None:
    fg.rss_file(output_path, pretty=True)
    _decorate_rss_file(
        output_path,
        site_url=site_url,
        generated_at=generated_at,
    )
    logger_method = logger.warning if failure else logger.info
    event = "feed.failure_generated" if failure else "feed.generated"
    logger_method(
        event,
        site=site_name,
        rss=str(output_path),
    )


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _decorate_rss_file(
    output_path: Path,
    site_url: str,
    generated_at: datetime,
) -> None:
    tree = ET.parse(output_path)
    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:  # pragma: no cover - defensive
        return

    _upsert_child_text(channel, "link", site_url)
    _upsert_child_text(channel, "pubDate", format_datetime(generated_at))
    _upsert_child_text(channel, "ttl", str(FEED_TTL_MINUTES))
    _upsert_child_text(
        channel,
        f"{{{SYNDICATION_NS}}}updatePeriod",
        FEED_UPDATE_PERIOD,
    )
    _upsert_child_text(
        channel,
        f"{{{SYNDICATION_NS}}}updateFrequency",
        FEED_UPDATE_FREQUENCY,
    )

    _ensure_hub_link(channel)

    tree.write(output_path, encoding="UTF-8", xml_declaration=True)


def _ensure_hub_link(channel: ET.Element) -> None:
    """Add WebSub hub link for real-time updates if not already present."""
    hub_tag = f"{{{ATOM_NS}}}link"
    for link in channel.findall(hub_tag):
        if link.attrib.get("rel") == "hub":
            return
    hub_link = ET.SubElement(channel, hub_tag)
    hub_link.set("href", WEBSUB_HUB_URL)
    hub_link.set("rel", "hub")


def _upsert_child_text(parent: ET.Element, tag: str, text: str) -> None:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.text = text
