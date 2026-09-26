"""Ad and boilerplate removal utilities for article enrichment."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Common ad selectors for Romanian/European news sites
# These patterns cover most ad placements on blogging sites
DEFAULT_AD_SELECTORS = [
    # Ad containers and placeholders
    ".ad",
    ".ad-container",
    ".ad-wrapper",
    ".advertisement",
    ".advertisement-placeholder",
    "[id*='ad-']",
    "[id*='advert']",
    "[class*='ad-']",
    "[class*='advert']",

    # Sidebar content (often contains ads)
    ".sidebar",
    "#sidebar",
    ".widget-ad",
    ".sidebarWidget",
    ".widget-armadaindicators",
    ".secondary-widget-area",
    ".sidebar-nav",
    ".sidebar-navigation",

    # Header/footer ads
    ".header-ad",
    ".footer-ad",
    ".mobile-ad",
    ".sticky-ad",
    ".top-ad",
    ".bottom-ad",

    # In-content ad placements
    ".in-content-ad",
    ".ad-in-content",
    ".before-content-ad",
    ".after-content-ad",
    ".inpost-ad",
    "ins.adsbygoogle",

    # Sponsored/promotional content
    ".sponsored",
    ".promoted",
    ".ad-sponsored",
    ".sponsor-content",
    ".sponsor-message",
    ".paid-content",
    ".advertisement-box",

    # Social sharing widgets (often ads in disguise)
    ".social-share",
    ".share-this",
    ".addtoany",
    ".social-buttons",
    ".share-buttons",
    ".social-media-share",
    ".post-sharing",

    # Newsletter/mail signup forms (often promotional)
    ".newsletter-signup",
    ".email-signup",
    ".subscription-form",
    ".popup-modal",
    ".popup-container",

    # Related posts sections (boilerplate, not main content)
    ".related-posts",
    ".related-articles",
    ".more-posts",
    ".you-may-like",
    ".related-content",
    ".similar-posts",
    ".also-like",

    # Comment sections (often contain ads)
    ".comments",
    "#comments",
    ".comment-section",
    ".comment-outer",

    # User interaction/like/comment buttons
    ".entry__footer",
    ".post-footer",
    ".article-footer",
    ".entry-meta",
    ".post-meta",
    ".article-meta",
    ".entry-tags",
    ".post-tags",
    ".tag-list",
    ".tag-chips",
    ".share",
    ".share__list",
    ".share__label",
    ".entry-tags__label",
    ".like-button",
    ".dislike-button",
    ".reaction-button",
    ".post-likes",
    ".likes-container",
    "[data-action='like']",
    "[data-action='dislike']",

    # Navigation and menus
    ".navigation",
    "nav.category-nav",
    ".main-navigation",
    ".footer-navigation",
    ".menu-bar",
    ".menu-container",

    # Author bio boxes (secondary content)
    ".author-bio",
    ".about-author",
    ".post-author",
    ".author-profile",

    # Tags and categories (metadata, not content)
    ".post-tags",
    ".categories",
    ".tags-links",
    ".tag-list",
    ".category-links",

    # Scripts and tracking pixels that might be in content
    "script:not(.src)",
]


def _get_stronger_ad_selectors() -> list[str]:
    """Get broader ad selectors for aggressive removal."""
    return [*DEFAULT_AD_SELECTORS, ".wrapper", ".container", ".content-area",
            ".main-area", ".article-body"]


def remove_ads_and_boilerplate(
    html: str,
    extra_selectors: list[str] | None = None,
    aggressive: bool = False,
) -> str:
    """Remove ads and boilerplate from HTML content.

    Args:
        html: The HTML content to clean
        extra_selectors: Additional CSS selectors to remove
        aggressive: Use more aggressive removal (removes sidebars, etc.)

    Returns:
        Cleaned HTML string
    """
    soup = BeautifulSoup(html, "html.parser")

    # Combine default, aggressive, and extra selectors
    if aggressive:
        all_selectors = DEFAULT_AD_SELECTORS + (extra_selectors or [])
    else:
        all_selectors = DEFAULT_AD_SELECTORS + (extra_selectors or [])

    for selector in all_selectors:
        try:
            for element in soup.select(selector):
                element.decompose()
        except Exception:
            pass

    return soup.decode_contents()


def _remove_wordpress_shortcodes(html: str) -> str:
    """Remove WordPress shortcodes from HTML content.

    Many shortcode outputs are unreadable in RSS feeds.
    """
    # Remove common WordPress shortcodes
    patterns = [
        r'\[su_note[^\]]*\].*?\[/su_note\]',
        r'\[su_box[^\]]*\].*?\[/su_box\]',
        r'\[su_button[^\]]*\].*?\[/su_button\]',
        r'\[youtube[^\]]*\].*?\[/youtube\]',
        r'\[vimeo[^\]]*\].*?\[/vimeo\]',
        r'\[social_link[^\]]*\]',
        r'\[dropcap[^\]]*\].*?\[/dropcap\]',
        r'\[highlight[^\]]*\].*?\[/highlight\]',
        r'\[spoiler[^\]]*\].*?\[/spoiler\]',
        r'\[toggle[^\]]*\].*?\[/toggle\]',
        r'\[accordion[^\]]*\].*?\[/accordion\]',
        r'\[tabs[^\]]*\].*?\[/tabs\]',
        r'\[carousel[^\]]*\].*?\[/carousel\]',
        r'\[gallery[^\]]*\]',
        r'\[post[^\]]*\]',
        r'\[posts[^\]]*\]',
        r'\[featured_material[^\]]*\]',
        r'\[testimonial[^\]]*\].*?\[/testimonial\]',
        r'\[message[^\]]*\].*?\[/message\]',
        r'\[panel[^\]]*\].*?\[/panel\]',
    ]

    result = html
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.DOTALL | re.IGNORECASE)

    return result


def _truncate_at_footer(html: str) -> str:
    """Truncate HTML content at common footer boundaries.

    Uses regex to find and remove content after interaction sections
    (like buttons, share widgets, tags, etc.).
    """
    # Remove WordPress shortcodes first
    html = _remove_wordpress_shortcodes(html)

    # Patterns that mark end of article content
    patterns = [
        # Like/dislike sections
        (r'Ți-a plăcut acest articol\?', ''),
        (r'alt\d+', ''),  # alt text for like buttons
        # Like button structures - complete removal
        (r'<div[^>]*class="[^"]*entry__like[^"]*"[^>]*>.*?</div>', '', re.DOTALL),
        (r'<button[^>]*class="[^"]*like-btn[^"]*"[^>]*>.*?</button>', '', True),
        # Share sections
        (r'<footer[^>]*class="[^"]*entry__footer[^"]*"[^>]*>.*?</footer>', '', re.DOTALL),
        (r'<div[^>]*class="[^"]*share[^"]*"[^>]*>.*?</div>', '', re.DOTALL),
        (r'<div[^>]*class="[^"]*entry-tags[^"]*"[^>]*>.*?</div>', '', re.DOTALL),
    ]

    result = html
    for pattern in patterns:
        if len(pattern) == 3:
            result = re.sub(pattern[0], pattern[1], result, flags=re.DOTALL | re.IGNORECASE)
        else:
            result = re.sub(pattern[0], pattern[1], result)

    return result


def extract_main_content(
    html: str,
    article_selectors: list[str] | None = None,
    max_length: int = 100_000,
) -> str:
    """Extract main article content using common selectors.

    Args:
        html: The HTML content to parse
        article_selectors: Specific selectors to look for article content
        max_length: Maximum content length to return (0 = no limit)

    Returns:
        Extracted article content HTML
    """
    soup = BeautifulSoup(html, "html.parser")

    # Common selectors for article content
    default_article_selectors = article_selectors or [
        "article",
        ".article-content",
        ".post-content",
        ".entry-content",
        ".content",
        "#content",
        ".main-content",
        ".blog-post-content",
        ".post-body",
        ".article-body",
        ".the-content",
        ".postText",
        ".txt",
        ".article-text",
        ".article",
        "[itemprop='articleBody']",
    ]

    for selector in default_article_selectors:
        try:
            elements = soup.select(selector)
            if elements:
                content = elements[0]
                # Remove ads from the extracted content
                for sel in DEFAULT_AD_SELECTORS:
                    for el in content.select(sel):
                        el.decompose()

                result = content.decode_contents()
                # Truncate at footer boundaries
                result = _truncate_at_footer(result)
                if max_length > 0 and len(result) > max_length:
                    result = result[:max_length]
                return result
        except Exception:
            continue

    # Fallback: return cleaned HTML if no article selector found
    result = remove_ads_and_boilerplate(html)
    result = _truncate_at_footer(result)
    if max_length > 0 and len(result) > max_length:
        result = result[:max_length]
    return result


def extract_featured_image(html: str) -> str | None:
    """Extract the featured image URL from HTML.

    Args:
        html: The HTML content to parse

    Returns:
        The featured image URL, or None if not found
    """
    soup = BeautifulSoup(html, "html.parser")

    # Check Open Graph meta tag first
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        return og_image["content"]

    # Check Twitter card meta tag
    twitter_image = soup.find("meta", attrs={"name": "twitter:image"})
    if twitter_image and twitter_image.get("content"):
        return twitter_image["content"]

    # Look for canonical featured image in article header
    featured_img = soup.find("img", class_="featured")
    if featured_img and featured_img.get("src"):
        return featured_img["src"]

    # Look for first image in article (skip small thumbnails)
    article_selectors = [
        "article",
        ".article-content",
        ".post-content",
        ".entry-content",
        ".content",
        "#content",
    ]

    for selector in article_selectors:
        try:
            article = soup.select_one(selector)
            if article:
                for img in article.find_all("img"):
                    src = img.get("src")
                    if src:
                        # Skip tiny thumbnails
                        width = img.get("width", 0)
                        height = img.get("height", 0)
                        try:
                            if int(width) >= 100 or int(height) >= 100:
                                return src if src.startswith("http") else f"https://example.com{src}"
                        except ValueError:
                            pass

                    # Return first valid image if we can't check dimensions
                    return src if src.startswith("http") else f"https://example.com{src}"
        except Exception:
            pass

    return None


def truncate_content(html: str, max_chars: int = 50_000) -> str:
    """Truncate HTML content while preserving structure.

    Args:
        html: HTML content to truncate
        max_chars: Maximum character count (not byte count)

    Returns:
        Truncated HTML content
    """
    soup = BeautifulSoup(html, "html.parser")

    total_chars = 0
    for element in soup.find_all(text=True, recursive=True):
        if not isinstance(element, str):
            continue
        text_len = len(element)
        total_chars += text_len
        if total_chars > max_chars:
            # Truncate this element's text
            remaining = max_chars - (total_chars - text_len)
            if remaining < 0:
                remaining = 0
            element.replace_with(element[:remaining])
            break

    return soup.decode_contents()


def remove_placeholder_svgs(html: str) -> str:
    """Remove placeholder SVG images from HTML content.

    These are often used as lazy-loading placeholders and should not appear
    in RSS feeds.
    """
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src and ("data:image/svg" in src or "base64" in src.lower()):
            img.decompose()
    return soup.decode_contents()