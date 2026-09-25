"""Ad and boilerplate removal utilities for article enrichment."""
from __future__ import annotations

from bs4 import BeautifulSoup

# Common ad selectors for Romanian/European news sites
# These patterns cover most ad placements on blogging sites like WordPress, Blogger, etc.
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

    # Header/footer ads
    ".header-ad",
    ".footer-ad",
    ".mobile-ad",
    ".sticky-ad",

    # In-content ad placements
    ".in-content-ad",
    ".ad-in-content",
    ".before-content-ad",
    ".after-content-ad",
    ".inpost-ad",

    # Sponsored/promotional content
    ".sponsored",
    ".promoted",
    ".ad-sponsored",
    ".sponsor-content",
    ".sponsor-message",

    # Social sharing widgets (often ads in disguise)
    ".social-share",
    ".share-this",
    ".addtoany",
    ".social-buttons",

    # Newsletter/mail signup forms (often promotional)
    ".newsletter-signup",
    ".email-signup",
    ".subscription-form",

    # Related posts sections (boilerplate, not main content)
    ".related-posts",
    ".related-articles",
    ".more-posts",
    ".you-may-like",

    # Comment sections (often contain ads)
    ".comments",
    "#comments",
    ".comment-section",

    # Navigation and menus
    ".navigation",
    "nav",
    ".menu",
    ".main-navigation",
    ".footer-navigation",

    # Author bio boxes (secondary content)
    ".author-bio",
    ".about-author",
    ".post-author",

    # Tags and categories (metadata, not content)
    ".post-tags",
    ".categories",
    ".tags-links",
]


def remove_ads_and_boilerplate(
    html: str,
    extra_selectors: list[str] | None = None,
) -> str:
    """Remove ads and boilerplate from HTML content.

    Args:
        html: The HTML content to clean
        extra_selectors: Additional CSS selectors to remove

    Returns:
        Cleaned HTML string
    """
    soup = BeautifulSoup(html, "html.parser")

    # Combine default and extra selectors
    all_selectors = DEFAULT_AD_SELECTORS + (extra_selectors or [])

    for selector in all_selectors:
        for element in soup.select(selector):
            element.decompose()

    return soup.decode_contents()


def extract_main_content(
    html: str,
    article_selectors: list[str] | None = None,
) -> str:
    """Extract main article content using common selectors.

    Args:
        html: The HTML content to parse
        article_selectors: Specific selectors to look for article content

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
    ]

    for selector in default_article_selectors:
        elements = soup.select(selector)
        if elements:
            # Take the first match (most sites have one main content area)
            content = elements[0]
            # Remove ads from the extracted content
            all_selectors = DEFAULT_AD_SELECTORS  # Use defaults, no extras
            for sel in all_selectors:
                for el in content.select(sel):
                    el.decompose()
            return content.decode_contents()

    # Fallback: return cleaned HTML if no article selector found
    return remove_ads_and_boilerplate(html, extra_selectors=article_selectors)


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

    # Look for first image in article
    article_selectors = [
        "article",
        ".article-content",
        ".post-content",
        ".entry-content",
        ".content",
        "#content",
    ]

    for selector in article_selectors:
        article = soup.select_one(selector)
        if article:
            img = article.find("img")
            if img and img.get("src"):
                # Resolve relative URLs
                src = img["src"]
                return src if src.startswith("http") else f"https://example.com{src}"

    return None