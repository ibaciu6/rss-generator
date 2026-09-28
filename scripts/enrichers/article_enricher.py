"""Article content enrichment for blog and news feeds."""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from core.logging_utils import get_logger
from scripts.enrichers.ad_remover import (
    extract_featured_image,
    extract_main_content,
    remove_ads_and_boilerplate,
    remove_placeholder_svgs,
    truncate_content,
)
from scripts.enrichers.removal_modules import apply_modules

# Image tag regex for finding/replacing images in descriptions
IMG_TAG_RE = re.compile(r'<img\s[^>]*>', re.IGNORECASE)

# WordPress derives thumbnails as `-{width}x{height}` before the extension, so a
# featured image is often a smaller copy of a photo the article already contains.
_WP_SIZE_RE = re.compile(r"-\d+x\d+(?=\.[a-z0-9]+$)", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_EMBED_RE = re.compile(r"<(iframe|video|object|embed)\b", re.IGNORECASE)


def _image_key(url: str) -> str:
    """Normalise an image URL so the same photo compares equal across CDN
    mirrors, size suffixes and query strings.

    `photo.jpg`, `photo-560x276.jpg`, `i0.wp.com/.../photo.jpg?resize=855,570`
    and `www.site.com/.../photo.jpg` are all the same image.
    """
    # Strip the scheme, host and query string -- only the path matters.
    path = url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
    return _WP_SIZE_RE.sub("", path)


def body_contains_image(body_html: str, img_url: str) -> bool:
    """True if the body already shows the same photo as `img_url`.

    The featured image is prepended to the body, so without this a WordPress
    article renders its lead photo twice: once as the prepended thumbnail and
    again at full size in the content.
    """
    key = _image_key(img_url)
    return any(_image_key(src) == key for src in _IMG_SRC_RE.findall(body_html))

# Maximum description length to prevent massive content from breaking readers
MAX_DESCRIPTION_LENGTH = 50_000

# Minimum visible text before an extraction is trusted. A page can fetch fine
# and still yield nothing: JS-only shells, paywalls, bot interstitials, and
# pages whose markup every selector strips. extract_main_content() then falls
# back to "clean the whole page", which is still a non-empty `<html>` shell, so
# a truthiness check accepts it and overwrites a perfectly good RSS excerpt with
# an empty document. Require real text, and keep the feed's own description when
# there isn't any.
logger = get_logger(__name__)

MIN_BODY_TEXT = 200

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def visible_text_length(html: str) -> int:
    """Approximate how much content a reader would see. Used only for the
    emptiness check - not a substitute for the extracted markup.

    An embed (iframe/video/object) counts as a full ``MIN_BODY_TEXT`` worth of
    content: an article that is mostly a video has little surrounding text but
    is still a real article, while a page whose only survivor is a stray
    tracking iframe is not.
    """
    text_len = len(_WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", html))).strip())
    return text_len + len(_EMBED_RE.findall(html)) * MIN_BODY_TEXT


# Bot-challenge interstitials. Deliberately high-confidence phrases only.
#
# The obvious keywords are traps: security feeds publish articles *about*
# CAPTCHAs and malware families with stages called "loader", so a bare
# `captcha` or `loader` test flags 11 perfectly good articles (schneier,
# securityaffairs, thehackernews, malwarebytes, torrentfreak, recorder, ...).
# Each phrase below is boilerplate that only ever appears on a challenge page.
_CHALLENGE_RES = (
    re.compile(r"one\s+moment,?\s*please", re.IGNORECASE),
    re.compile(r"just\s+a\s+moment\b", re.IGNORECASE),
    re.compile(r"attention\s+required\s*[|!\s]+\s*cloudflare", re.IGNORECASE),
    re.compile(r"checking\s+your\s+browser\s+before\s+accessing", re.IGNORECASE),
    re.compile(r"enable\s+javascript\s+and\s+cookies\s+to\s+continue", re.IGNORECASE),
    re.compile(r"ddos\s+protection\s+by\b", re.IGNORECASE),
    re.compile(r"please\s+wait\s+while\s+your\s+request\s+is\s+being\s+verified", re.IGNORECASE),
    re.compile(r"you\s+have\s+been\s+blocked|ray\s+id:\s*[0-9a-f]{6,}", re.IGNORECASE),
    # The Register's interstitial: a bare "Are we human?" <title> beside a
    # <div class="wicketkeeper" data-input-name="solution"> that renders nothing
    # without JavaScript. It carried neither of the phrases above, so 25 of its
    # 30 items were published as this 1.2 KB of challenge markup: a feed item
    # whose entire content is a robot check, and a 13-char "excerpt" once the
    # tags are counted. Matched on the two markers that make it recognisable.
    re.compile(r'class="wicketkeeper"', re.IGNORECASE),
    re.compile(r"<title>\s*are\s+we\s+human\s*\?*\s*</title>", re.IGNORECASE),
)


def looks_like_challenge(html: str) -> bool:
    """True when the response is a bot-challenge page rather than the article.

    Matched against the raw page rather than the extracted body: the
    interstitial's distinctive copy often sits in ``<noscript>`` or ``<title>``,
    which the removal modules would delete before any later check ran.
    """
    if not html:
        return False
    return any(rx.search(html) for rx in _CHALLENGE_RES)


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

    # Named removal modules to apply after ad removal, in order. Each is
    # implemented once in removal_modules.py and shared across feeds.
    removals: list[str] = field(default_factory=list)
    # Where to get the article body when the article page is unreachable. See
    # `SiteConfig.article_source`; any failure falls back to the page fetch.
    article_source: dict | None = None


# An optional proxy for article fetches, from the same environment variable the
# scraper uses. This exists because article pages and feed URLs are not treated
# the same way by bot protection: hackread serves its *feed* over the bare host
# with a 200 and answers the same host's article pages with 403 from a
# datacenter address, so the feed generates and then every item is a stub. The
# scraper's fetcher has always read this variable; article enrichment did not,
# so even setting the secret only helped half the pipeline.
#
# Unset is the normal case and the correct one for a local run -- there is no
# reason to route a residential fetch through a proxy. It is read per call
# rather than at import so a test can set it.
def _proxy_kwargs() -> dict:
    proxy = (os.environ.get("RSS_GENERATOR_PROXY_URL") or "").strip()
    return {"proxy": proxy} if proxy else {}


def _cap_output(html: str, cap: int) -> str:
    """Cut `html` to at most `cap` characters, never mid-tag.

    `truncate_content` bounds the visible text, which is not the same thing as
    bounding the string: markup sits on top of prose, so a text-bounded body
    still came back at 50,005 bytes against a 50,000 cap. This is the second
    half of enforcing the cap -- on the output, by cutting rather than by
    refusing to publish.

    The cut lands just before the last `<` inside the cap so no tag is left
    half-written. Unclosed elements are not a problem: the body is HTML
    embedded in a feed item and the reader closes them at the end of the block.
    """
    if len(html) <= cap:
        return html
    head = html[:cap]
    last_open = head.rfind("<")
    # Only retreat if doing so still leaves most of the budget, otherwise a
    # pathological run of markup would cut the prose away entirely.
    return head[:last_open] if last_open > cap * 0.9 else head


async def _fetch_article_body(
    url: str,
    source: dict | None,
    client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> tuple[str | None, str]:
    """(html, how) for an article, from the page or from a configured source.

    ``how`` is "page", "api" or "none", and exists so the run can say which
    route an item came in on -- an article silently fetched a different way
    than every other one is exactly the kind of thing that makes a feed hard
    to reason about.
    """
    page = await _fetch_article_page(url, client=client, timeout=timeout)
    if page:
        return page, "page"
    if not source:
        return None, "none"
    html = await _fetch_from_source(url, source, client=client, timeout=timeout)
    return (html, "api") if html else (None, "none")


def _wordpress_slug(url: str) -> str:
    """The post slug from a permalink like ``/2026/09/18/some-title/``.

    WordPress will also resolve a bare slug, so a dated prefix is simply not
    part of it. Falls back to the last path segment, which is the slug for
    every permalink shape WordPress uses except the plain ``?p=123`` form.
    """
    path = urlsplit(url).path.strip("/")
    if not path:
        return ""
    return path.rsplit("/", 1)[-1]


async def _fetch_from_source(
    url: str, source: dict, client: httpx.AsyncClient | None = None, timeout: float = 15.0
) -> str | None:
    """Fetch an article body from a configured alternative source.

    Only WordPress is implemented, because it is the only shape measured worth
    supporting: a site can block its article pages to a datacenter address and
    still answer its own REST API, and that API returns the article as
    rendered content -- 15,538 characters of it for a hackread item whose page
    returns 403 from CI. It is content, not a rendering, so it is not subject
    to the page rules.

    Anything unexpected returns None, and the caller falls back to the page
    fetch. This must never make a feed worse than it was.
    """
    kind = str(source.get("type") or "").strip().lower()
    if kind != "wordpress":
        return None
    api = str(source.get("api") or "").strip()
    slug = _wordpress_slug(url)
    if not api or not slug:
        return None

    request = f"{api.rstrip('/')}/?slug={quote(slug)}"
    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, **_proxy_kwargs()
            ) as one_shot:
                resp = await one_shot.get(request)
        else:
            resp = await client.get(request, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return None
        posts = resp.json()
    except Exception as exc:
        logger.info("enrich.source_failed", url=url[:100], source=kind, error=str(exc)[:120])
        return None
    if not isinstance(posts, list) or not posts:
        return None
    rendered = (posts[0].get("content") or {}).get("rendered")
    return rendered if isinstance(rendered, str) and rendered.strip() else None


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
        # `httpx.get` is synchronous: awaiting it raises TypeError, which the
        # except below swallowed into a plain "fetch failed". Every call
        # without a shared client therefore returned None and reported itself
        # as a network error. The enrich loop always passes a client, which is
        # why production never showed it -- a caller that does not, gets
        # nothing and is told the network is at fault.
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, **_proxy_kwargs()
            ) as one_shot:
                resp = await one_shot.get(url)
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

    # `kept_excerpt` is the number that matters to a reader: items that still
    # hold only the two-paragraph excerpt the site shipped in its own RSS,
    # because the fetch failed, a bot wall answered, or extraction came back
    # with less text than the excerpt we already had. It was folded into
    # `skipped`, which is never surfaced, so a feed could be "enriched"
    # successfully while every single item stayed a stub.
    stats: dict = {
        "items": 0,
        "enriched": 0,
        "errors": 0,
        "skipped": 0,
        "kept_excerpt": 0,
        "fetch_failed": 0,
        "challenge": 0,
        # Items whose body came from the site's own API rather than its page.
        # Said in the run summary, because it is surprising and it is the only
        # reason those items are full articles at all.
        "from_api": 0,
    }
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
        html, how = await _fetch_article_body(
            url, config.article_source, client=client, timeout=config.fetch_timeout
        )
        if how == "api":
            stats["from_api"] += 1
        if not html:
            stats["skipped"] += 1
            stats["fetch_failed"] += 1
            stats["kept_excerpt"] += 1
            continue

        # A bot-challenge page is a 200-OK response that is not the article.
        # MIN_BODY_TEXT cannot catch it: the Cloudflare interstitial carries a
        # spinner, keyframes and several sentences, so it sails past the
        # emptiness check and overwrites a perfectly good RSS excerpt (hoinaru
        # and razvanbb served nothing but "One moment, please..." for 15 items).
        # Refuse it before extraction so the feed keeps what it already had.
        if looks_like_challenge(html):
            stats["skipped"] += 1
            stats["challenge"] += 1
            stats["kept_excerpt"] += 1
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

        # Apply the named removal modules. These target chrome that CSS
        # selectors cannot: comment sections, the Akismet notice, sponsor
        # blocks with hashed Tailwind classes, emoji images.
        if config.removals:
            cleaned_html = apply_modules(cleaned_html, config.removals)

        # Cap again, after every transform rather than only before them, and this
        # time enforce it on the output rather than discarding the body.
        #
        # `remove_ads_and_boilerplate` and the removal modules both *add* bytes
        # as well as removing them, so a body already truncated to the cap can
        # end up back over it. The final gate used to require the body to be
        # under the cap as a condition of being published at all, so a body that
        # finished 21 bytes over lost everything: buletin-de-bucuresti was
        # measured at 50,021 bytes carrying 48,493 characters of article, and
        # all ten of its items shipped as their site excerpt. Nothing said the
        # cap was the reason -- the item looked like an extraction failure.
        #
        # `truncate_content` bounds the *visible text*; the gate was comparing
        # the *string length*, and markup is the difference between the two --
        # a text-bounded body came back at 50,005 bytes. So the bound has to be
        # applied to the output as well, and the only way to do that without
        # throwing the article away is to cut it.
        if len(cleaned_html) > MAX_DESCRIPTION_LENGTH:
            cleaned_html = _cap_output(cleaned_html, MAX_DESCRIPTION_LENGTH)

        # Build new description
        new_parts = []

        # Add featured image if configured
        if config.add_featured_image:
            featured_img = extract_featured_image(html)
            if featured_img and not body_contains_image(cleaned_html, featured_img):
                new_parts.append(_build_featured_image_tag(featured_img))

        # Length is no longer a gate: the body was capped immediately above, so
        # anything still over the limit has been truncated to it rather than
        # discarded. What remains is the question that actually matters -- is
        # there an article in here, or is this the page's chrome?
        if (
            cleaned_html
            and visible_text_length(cleaned_html) >= MIN_BODY_TEXT
        ):
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
            # The cap binds the description the reader actually loads, which is
            # the assembled string, not `cleaned_html`. The featured image and
            # any retained excerpt are added after the body was capped, and
            # together they put it back over: buletin-de-bucuresti measured
            # 50,253 bytes against a 50,000 cap with a 253-byte featured image
            # on the front. Capping the body and then declaring the item
            # in-bounds measures the wrong string.
            if len(full_description) > MAX_DESCRIPTION_LENGTH:
                full_description = _cap_output(full_description, MAX_DESCRIPTION_LENGTH)

            # No "Read more at source" trailer, and no source link in the reader
            # panel either: the full body is already inline, and there is
            # deliberately nothing left to follow.

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
            # Nothing usable came back. Leave the feed's own description alone -
            # an excerpt written by the site is always better than an empty
            # document shell. See MIN_BODY_TEXT. Counted separately because
            # from a reader's side this is indistinguishable from a full
            # article: the item is a stub, and nothing else says so.
            stats["skipped"] += 1
            stats["kept_excerpt"] += 1

    if changed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)

    return changed, stats

