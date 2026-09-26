"""Article content enrichment for blog and news feeds."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx

from scripts.enrichers.ad_remover import (
    extract_featured_image,
    extract_main_content,
    remove_ads_and_boilerplate,
    remove_placeholder_svgs,
    truncate_content,
)

# Image tag regex for finding/replacing images in descriptions
IMG_TAG_RE = re.compile(r'<img\s[^>]*>', re.IGNORECASE)

# Maximum description length to prevent massive content from breaking readers
MAX_DESCRIPTION_LENGTH = 50_000


@dataclass
class ArticleEnrichConfig:
    """Configuration for article feed enrichment."""

    # Selectors to find main article content
    content_selectors: list[str] = field(default_factory=list)

    # Additional selectors to remove (ads, sidebars, etc.)
    ad_selectors: list[str] = field(default_factory=list)

    # Maximum content length to extract (0 = no limit, 50000 = 50KB)
    max_content_length: int = 50_000

    # Whether to replace summary with full content or prepend to it
    replace_summary: bool = True

    # Add featured image to the description
    add_featured_image: bool = True

    # Fetch timeout in seconds
    fetch_timeout: float = 15.0

    # Aggressive ad removal (removes sidebars, nav, etc.)
    aggressive_mode: bool = True


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
    return (
        f'<img src="{img_url}" '
        f'style="max-width:300px;max-height:450px;width:auto;'
        f'height:auto;object-fit:contain;display:block;border-radius:4px;" '
        f'width="300"/>'
    )


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

        # Extract main content with ad removal
        article_html = extract_main_content(
            html,
            article_selectors=config.content_selectors or None,
            max_length=config.max_content_length,
        )

        # Apply ad removal
        cleaned_html = remove_ads_and_boilerplate(
            article_html,
            extra_selectors=config.ad_selectors,
            aggressive=config.aggressive_mode,
        )

        # Truncate if still too long
        if len(cleaned_html) > MAX_DESCRIPTION_LENGTH:
            cleaned_html = truncate_content(cleaned_html, MAX_DESCRIPTION_LENGTH)

        # Remove placeholder SVGs
        cleaned_html = remove_placeholder_svgs(cleaned_html)

        # Build new description
        new_parts = []

        # Add featured image if configured
        if config.add_featured_image:
            featured_img = extract_featured_image(html)
            if featured_img:
                new_parts.append(_build_featured_image_tag(featured_img))

        if cleaned_html and len(cleaned_html) <= MAX_DESCRIPTION_LENGTH:
            if config.replace_summary:
                new_parts.append(cleaned_html)
            else:
                # Prepend new content to old description
                desc_el = item.find("description")
                old_desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
                if old_desc:
                    new_parts.insert(0, old_desc)
                new_parts.append(cleaned_html)

            full_description = "".join(new_parts)

            # Add source link if we have a URL
            source_url = link_el.text.strip() if link_el is not None and link_el.text else None
            if source_url and source_url not in full_description:
                full_description += f'<br><br><a href="{source_url}">Read more at source</a>'

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
        else:
            stats["skipped"] += 1

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)

    return changed, stats

