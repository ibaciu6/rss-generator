from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

from feedgen.feed import FeedGenerator

from core.logging_utils import get_logger
from scraper.parser import ParsedItem


logger = get_logger(__name__)

FAILURE_TITLE_SUFFIX = " (unavailable)"


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

    fg = _build_feed(
        feed_title=site_name,
        site_url=site_url,
        output_path=output_path,
        description=f"Feed generated for {site_name}",
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

    _write_feed(fg, output_path, site_name, failure=False)


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
    )

    entry = fg.add_entry()
    entry.id(f"{site_url}#generation-status")
    entry.title("Feed generation failed")
    entry.link(href=site_url)
    entry.description(description)
    entry.content(f"<p>{escape(description)}</p>", type="html")
    entry.pubDate(failed_at)
    entry.updated(failed_at)

    _write_feed(fg, output_path, site_name, failure=True)


def is_failure_feed_title(title: Optional[str]) -> bool:
    return bool(title and title.endswith(FAILURE_TITLE_SUFFIX))


def _build_feed(
    feed_title: str,
    site_url: str,
    output_path: Path,
    description: str,
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
    return fg


def _write_feed(
    fg: FeedGenerator,
    output_path: Path,
    site_name: str,
    failure: bool,
) -> None:
    fg.rss_file(output_path, pretty=True)
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
