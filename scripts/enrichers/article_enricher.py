"""Article content enrichment for blog and news feeds."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx

from core.logging_utils import get_logger
from scripts.enrichers.ad_remover import extract_featured_image, extract_main_content

logger = get_logger(__name__)

FEEDS_DIR = Path(__file__).resolve().parent.parent.parent / "feeds"

# Image tag regex for finding/replacing images in descriptions
IMG_TAG_RE = re.compile(r"<img\s[^>]*>", re.IGNORECASE)


@dataclass
class ArticleEnrichConfig:
    """Configuration for article feed enrichment."""

    # Selectors to find main article content
    content_selectors: list[str] = field(default_factory=list)

    # Additional selectors to remove (ads, sidebars, etc.)
    ad_selectors: list[str] = field(default_factory=list)

    # Maximum content length to extract (0 = no limit)
    max_content_length: int = 500_000

    # Whether to replace summary with full content or prepend to it
    replace_summary: bool = True

    # Add featured image to the description
    add_featured_image: bool = True

    # Fetch timeout in seconds
    fetch_timeout: float = 15.0


async def _fetch_article_page(
    url: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> str | None:
    """Fetch an article page and return the HTML content.

    Args:
        url: The URL to fetch
        client: Optional shared httpx client
        timeout: Request timeout in seconds

    Returns:
        HTML content string or None if fetch failed
    """
    if client is None:
        try:
            resp = await httpx.get(url, timeout=timeout, follow_redirects=True)
            return resp.text if resp.status_code == 200 else None
        except Exception:
            return None

    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None


def _build_featured_image_tag(img_url: str) -> str:
    """Build an HTML img tag for a featured image."""
    if not img_url:
        return ""
    # Normalize URL
    if not img_url.startswith("http"):
        img_url = f"https://example.com{img_url}"
    return f'<img src="{img_url}" style="max-width:300px;max-height:450px;width:auto;height:auto;object-fit:contain;display:block;border-radius:4px;" width="300"/>'


async def enrich_article_feed(
    path: Path,
    *,
    client: httpx.AsyncClient | None = None,
    config: ArticleEnrichConfig | None = None,
    base_url: str | None = None,
) -> tuple[bool, dict]:
    """Enrich an article/blog feed with full content and media.

    Fetches each item's URL, extracts the main article content,
    removes ads/boilerplate, and updates the description.

    Args:
        path: Path to the RSS feed XML file
        client: Optional shared httpx client for fetching
        config: Enrichment configuration
        base_url: Base URL for resolving relative links

    Returns:
        Tuple of (changed, stats) where stats contains:
        - items: total items processed
        - enriched: items with content added
        - errors: fetch errors
        - skipped: items without links or content
    """
    if config is None:
        config = ArticleEnrichConfig()

    stats: dict = {"items": 0, "enriched": 0, "errors": 0, "skipped": 0}
    changed = False

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

    for item in channel.findall("item"):
        stats["items"] += 1

        link_el = item.find("link")
        if link_el is None or not link_el.text:
            stats["skipped"] += 1
            continue

        url = link_el.text.strip()
        if not url.startswith(("http://", "https://")):
            if base_url:
                url = urljoin(base_url, url)
            else:
                stats["skipped"] += 1
                continue

        # Fetch the article page
        html = await _fetch_article_page(url, client=client, timeout=config.fetch_timeout)
        if not html:
            stats["skipped"] += 1
            continue

        # Extract main content
        article_html = extract_main_content(html, article_selectors=config.content_selectors or None)

        # Remove ads and boilerplate
        cleaned_html = article_html
        for sel in (config.ad_selectors or []) + ([".ad", ".sidebar"]):  # Include defaults
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(cleaned_html, "html.parser")
                for el in soup.select(sel):
                    el.decompose()
                cleaned_html = soup.decode_contents()
            except Exception:
                pass

        # Get old description
        desc_el = item.find("description")
        old_desc = desc_el.text if desc_el is not None else ""

        # Build new description
        new_parts = []

        # Add featured image if configured
        if config.add_featured_image:
            featured_img = extract_featured_image(html)
            if featured_img:
                new_parts.append(_build_featured_image_tag(featured_img))

        # Add article content
        if cleaned_html and len(cleaned_html) <= config.max_content_length:
            new_parts.append(cleaned_html)

            if not config.replace_summary and old_desc:
                # Prepend new content to old description
                new_parts.insert(0, old_desc)
        else:
            # Fall back to original description if content too long or nothing extracted
            if old_desc:
                new_parts.append(old_desc)
            continue

        # Combine parts
        full_description = "".join(new_parts)

        # Update description elements
        desc_el = item.find("description")
        if desc_el is None:
            desc_el = ET.SubElement(item, "description")
        desc_el.text = full_description

        # Also update encoded content if present (for feeds that use it)
        encoded_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        if encoded_el is not None:
            encoded_el.text = full_description

        stats["enriched"] += 1
        changed = True

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)

    return changed, stats


async def enrich_feeds_with_client(
    feeds_dir: Path,
    categories: list[str] | None = None,
) -> dict[str, tuple[bool, dict]]:
    """Enrich multiple feeds concurrently using a shared client.

    Args:
        feeds_dir: Directory containing feed XML files
        categories: Categories to enrich (None = all)

    Returns:
        Dict mapping feed names to (changed, stats) tuples
    """
    results: dict[str, tuple[bool, dict]] = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        feed_files = list(feeds_dir.glob("*.xml"))

        # Filter by category if specified
        if categories:
            from core.config import load_config
            config = load_config(Path("config/sites.yaml"))
            feed_files = [
                f
                for f in feed_files
                if any(s.feed_file == f.name and s.category in categories for s in config.sites)
            ]

        for feed_file in feed_files:
            feed_name = feed_file.stem
            logger.info("enrich_article", feed=feed_name)

            try:
                changed, stats = await enrich_article_feed(feed_file, client=client)
                results[feed_name] = (changed, stats)
                status = "OK" if changed else "--"
                logger.info("enrich_article.done", feed=feed_name, status=status, stats=stats)
            except Exception as e:
                logger.error("enrich_article.error", feed=feed_name, error=str(e))
                results[feed_name] = (False, {"error": str(e)})

    return results