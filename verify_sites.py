#!/usr/bin/env python3
"""
Verify duplicate sites by fetching them with Playwright like the RSS generator does.
"""
from playwright.sync_api import sync_playwright
import sys


def fetch_with_playwright(url: str) -> dict:
    """Fetch a URL with Playwright and return analysis."""
    print(f"\nFetching: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(2000)  # Wait for dynamic content

            # Extract key identifiers
            title = page.title()

            # Check for WordPress
            is_wp = bool(page.query_selector('link[href*="wp-content"]') or
                        page.query_selector('script[src*="wp-"]'))

            # Get footer/copyright
            footer = ""
            footer_elem = page.query_selector('footer')
            if footer_elem:
                footer = footer_elem.inner_text()[:200]

            # Count movie/content items
            item_count = 0
            item_selectors = [
                'article.item.movies',
                'div.flw-item',
                'a.group.block[href*="/movie/"]',
                'div.movie-card'
            ]
            for selector in item_selectors:
                items = page.query_selector_all(selector)
                if items:
                    item_count = len(items)
                    break

            # Get some movie titles
            titles = []
            title_selectors = [
                'h3.film-name a',
                'div.data h3',
                'h2.movie-title',
                'img[alt]'
            ]
            for selector in title_selectors:
                elems = page.query_selector_all(selector)
                if elems:
                    titles = [e.inner_text() if 'img' not in selector else e.get_attribute('alt')
                             for e in elems[:5]]
                    break

            # Get meta tags
            meta_generator = page.query_selector('meta[name="generator"]')
            generator = meta_generator.get_attribute('content') if meta_generator else None

            result = {
                'url': url,
                'status': 'success',
                'title': title,
                'is_wordpress': is_wp,
                'generator': generator,
                'item_count': item_count,
                'sample_titles': titles,
                'footer_excerpt': footer[:150] if footer else None
            }

            browser.close()
            return result

        except Exception as e:
            browser.close()
            return {
                'url': url,
                'status': 'error',
                'error': str(e)
            }


def main():
    # Sites from duplicate groups
    sites = [
        ('FilmeHD.cc', 'https://filmehd.cc/filme/'),
        ('SerialeOnline.live', 'https://serialeonline.live/filme/'),
        ('FilmeHD.to', 'https://filmehd.to/filme/'),
        ('FSOnline', 'https://www3.fsonline.app/film/'),
        ('Seriale-Online.net', 'https://seriale-online.net/filme/'),
    ]

    print("=" * 80)
    print("WEBSITE VERIFICATION USING PLAYWRIGHT")
    print("=" * 80)

    results = []
    for name, url in sites:
        result = fetch_with_playwright(url)
        result['name'] = name
        results.append(result)

    # Print analysis
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)

    for r in results:
        print(f"\n📍 {r['name']}")
        print(f"   URL: {r['url']}")

        if r['status'] == 'error':
            print(f"   ❌ Error: {r['error']}")
            continue

        print(f"   ✅ Status: {r['status']}")
        print(f"   Page Title: {r['title']}")
        print(f"   WordPress: {'Yes' if r['is_wordpress'] else 'No'}")
        if r['generator']:
            print(f"   Generator: {r['generator']}")
        print(f"   Items Found: {r['item_count']}")

        if r['sample_titles']:
            print(f"   Sample Titles:")
            for title in r['sample_titles'][:3]:
                print(f"     • {title}")

        if r['footer_excerpt']:
            print(f"   Footer: {r['footer_excerpt'][:100]}...")

    # Compare for clones
    print("\n" + "=" * 80)
    print("CLONE DETECTION")
    print("=" * 80)

    successful = [r for r in results if r['status'] == 'success' and r['sample_titles']]

    for i, r1 in enumerate(successful):
        for r2 in successful[i+1:]:
            titles1 = set(t.lower().strip() for t in r1['sample_titles'])
            titles2 = set(t.lower().strip() for t in r2['sample_titles'])

            common = titles1 & titles2
            if common:
                similarity = len(common) / len(titles1 | titles2) * 100
                print(f"\n{r1['name']} ↔ {r2['name']}")
                print(f"  Similarity: {similarity:.1f}% ({len(common)}/{len(titles1 | titles2)} titles)")
                if similarity > 70:
                    print(f"  🚨 HIGH - Likely clones/mirrors")
                elif similarity > 30:
                    print(f"  ⚠️  MEDIUM - Possible overlap")


if __name__ == '__main__':
    main()
