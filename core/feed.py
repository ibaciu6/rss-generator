from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from feedgen.feed import FeedGenerator

from core.logging_utils import get_logger
from scraper.parser import ParsedItem


logger = get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def generate_rss_and_atom(
    items: Iterable[ParsedItem],
    site_name: str,
    site_url: str,
    category: Optional[str],
    output_path: Path,
) -> None:
    """
    Generate both RSS 2.0 and Atom feeds into `feeds/`.
    We write a single XML file that is valid as both RSS and Atom using feedgen's generators.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fg = FeedGenerator()
    fg.id(site_url)
    fg.title(site_name)
    fg.link(href=site_url, rel="alternate")
    fg.link(href=str(output_path.name), rel="self")
    fg.description(f"Feed generated for {site_name}")
    fg.language("en")

    for item in items:
        fe = fg.add_entry()
        fe.id(item.link)
        fe.title(item.title)
        fe.link(href=item.link)
        if item.description:
            fe.description(item.description)
            fe.content(item.description, type="html")
        if item.pub_date:
            fe.pubDate(item.pub_date)
            fe.updated(item.pub_date)
        if category:
            fe.category(term=category)

    rss_path = output_path
    atom_path = output_path.with_suffix(".atom.xml")

    fg.rss_file(rss_path, pretty=True)
    fg.atom_file(atom_path, pretty=True)

    logger.info(
        "feed.generated",
        site=site_name,
        rss=str(rss_path),
        atom=str(atom_path),
    )


