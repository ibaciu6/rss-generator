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


# Selectors applied only in aggressive mode, on top of DEFAULT_AD_SELECTORS.
#
# Invariant: every selector here must be UI chrome (sidebars, share bars, related
# posts, comment forms), never a content container. This function runs on the
# ALREADY-extracted article body, so a container selector such as ".wrapper" or
# ".article-body" deletes exactly the text extract_main_content() just selected.
# Kept deliberately narrow for that reason: article_enricher sets
# aggressive_mode=True by default, so this list is applied to every article feed.
AGGRESSIVE_AD_SELECTORS: list[str] = [
    # Secondary sidebars / off-canvas columns
    ".sidebar-secondary",
    "#secondary",
    ".left-sidebar",
    ".right-sidebar",
    ".site-sidebar",
    # Social share / reaction bars
    ".sharedaddy",
    ".addtoany_list",
    ".share-buttons",
    ".social-share",
    # Related posts / read-next blocks
    ".yarpp-related",
    ".jp-relatedposts",
    ".related-posts",
    # Comments and breadcrumbs
    "#comments",
    ".comments-area",
    ".comment-respond",
    ".breadcrumbs",
    # Cookie / consent / promo chrome
    ".cookie-notice",
    ".cookie-consent",
    ".gdpr",
    ".newsletter-signup",
    ".author-bio",
    ".back-to-top",
    # Accessibility helpers (invisible text that is noise in a feed)
    ".skip-link",
    ".screen-reader-text",
]


def _get_stronger_ad_selectors() -> list[str]:
    """Full selector set for aggressive removal (defaults + aggressive extras)."""
    return [*DEFAULT_AD_SELECTORS, *AGGRESSIVE_AD_SELECTORS]


def remove_ads_and_boilerplate(
    html: str,
    extra_selectors: list[str] | None = None,
    aggressive: bool = False,
) -> str:
    """Remove ads and boilerplate from HTML content.

    Args:
        html: The HTML content to clean. Note this is normally the *already
            extracted* article body, not the full page — see
            AGGRESSIVE_AD_SELECTORS for why content containers are off-limits.
        extra_selectors: Additional CSS selectors to remove
        aggressive: Also remove theme boilerplate (sidebars, share bars,
            related-post and comment blocks). Safe on extracted content.

    Returns:
        Cleaned HTML string
    """
    soup = BeautifulSoup(html, "html.parser")

    # Combine default, aggressive, and extra selectors
    base_selectors = _get_stronger_ad_selectors() if aggressive else DEFAULT_AD_SELECTORS
    all_selectors = base_selectors + (extra_selectors or [])

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


# Images that are never a featured image: Facebook emoji, placeholder SVGs and
# the theme's own UI assets under /wp-content/themes/. Without this, whichever
# of those happens to be og:image or the first <img> gets prepended as the
# featured image (razvanbb's emoji, schneier's rss.png). Theme assets are
# filtered here as well as in the `theme-icons` module because the featured
# image is prepended *after* the modules run.
_NOT_FEATURED_IMG_RE = re.compile(
    r"fbcdn\.net|emoji\.php|data:image/svg|/wp-content/themes/", re.IGNORECASE
)


def _is_featured_candidate(src: str) -> bool:
    return bool(src) and not _NOT_FEATURED_IMG_RE.search(src)


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
    if og_image and og_image.get("content") and _is_featured_candidate(og_image["content"]):
        return og_image["content"]

    # Check Twitter card meta tag
    twitter_image = soup.find("meta", attrs={"name": "twitter:image"})
    if twitter_image and twitter_image.get("content") and _is_featured_candidate(twitter_image["content"]):
        return twitter_image["content"]

    # Look for canonical featured image in article header
    featured_img = soup.find("img", class_="featured")
    if featured_img and _is_featured_candidate(featured_img.get("src") or ""):
        return featured_img["src"]

    # Look for first image in article (skip small thumbnails, emoji and
    # placeholder SVGs)
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
                    src = img.get("src") or img.get("data-src") or ""
                    if not _is_featured_candidate(src):
                        continue
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

    ``max_chars`` caps the visible text. Every element that starts before the cap
    is kept; everything after it is dropped, so the result never exceeds the cap.
    Trimming only the single text node that crosses the cap - which is all this
    used to do - left the rest of the document in place, so a page dominated by
    one huge text node (a ``<style>`` block) sailed straight past the limit.
    """
    soup = BeautifulSoup(html, "html.parser")
    nodes = [n for n in soup.find_all(string=True, recursive=True) if isinstance(n, str)]

    total = 0
    cutoff = None
    remaining = 0
    for index, node in enumerate(nodes):
        length = len(node)
        if total + length > max_chars:
            cutoff = index
            remaining = max_chars - total
            break
        total += length

    if cutoff is None:
        return soup.decode_contents()

    nodes[cutoff].replace_with(nodes[cutoff][:remaining])
    for node in nodes[cutoff + 1:]:
        node.replace_with("")
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