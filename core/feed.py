from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from feedgen.feed import FeedGenerator

from core.logging_utils import get_logger
from scraper.parser import ParsedItem


logger = get_logger(__name__)

FAILURE_TITLE_SUFFIX = " (unavailable)"
FEED_TTL_MINUTES = 30
FEED_UPDATE_PERIOD = "hourly"
FEED_UPDATE_FREQUENCY = "1"
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
SYNDICATION_NS = "http://purl.org/rss/1.0/modules/syndication/"

ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("sy", SYNDICATION_NS)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
            fe.description(item.description)
            fe.content(item.description, type="html")
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
    description = (
        f"Last generation attempt failed on {failed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}. "
        f"Reason: {error_message}"
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
    # Keep the self-link relative to the feed file location so it resolves
    # correctly on GitHub Pages project sites like /rss-generator/feeds/*.xml.
    fg.link(href=output_path.name, rel="self")
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

    tree.write(output_path, encoding="UTF-8", xml_declaration=True)


def _upsert_child_text(parent: ET.Element, tag: str, text: str) -> None:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.text = text
