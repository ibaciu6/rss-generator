# RSS Generator — Architecture & Docs

## Overview

Python RSS feed generator that scrapes streaming sites (Romanian + English) via GitHub Actions, publishing feeds to GitHub Pages.

---

## Architecture

```
GitHub Actions (cron)
  └─ GenerationEngine
       ├─ Fetcher (http → cloudscraper → Playwright fallback)
       ├─ Parser (elementpath XPath 2.0 → lxml XPath 1.0)
       ├─ DedupStore (URL-based dedup across runs)
       ├─ RSS Feed writer
       └─ WordPress API reader (fallback)
```

---

## Adding a New Site

### 1. Identify site type

| Site Type | Example | Strategy |
|-----------|---------|----------|
| Server-rendered HTML | filmehd.to, xfilme.ro | `method: http` or `cloudscraper` — XPath on raw HTML |
| WordPress | portalultautv.info | `method: http` — auto-detects `/wp-json/wp/v2/posts` |
| Next.js SPA | cineby.sc, cinemaos.live | `method: playwright` — needs `playwright_wait_selector` |
| Cloudflare-protected | ridomovies.is | `method: cloudscraper` or `playwright` |
| Native RSS | any `/feed/` | auto-detected — no selectors needed |

### 2. Test the site

```bash
# Quick test: does the page load?
python3 -c "
import httpx
r = httpx.get('https://example.com', follow_redirects=True, timeout=10)
print(r.status_code, len(r.content))
"

# Check for embedded JSON (SPAs)
grep -o '__NEXT_DATA__\|__NUXT__\|__INITIAL_STATE__\|__next_f.push'

# Test with cloudscraper for Cloudflare sites
python3 -c "
import cloudscraper
s = cloudscraper.create_scraper()
r = s.get('https://example.com', timeout=10)
print(r.status_code, len(r.content))
"

# Test with Playwright for SPAs
python3 << 'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://example.com', wait_until='load', timeout=30000)
    page.wait_for_timeout(5000)
    links = page.query_selector_all('a[href*=\"/movie/\"], a[href*=\"/tv/\"]')
    print(f'Movie/TV links: {len(links)}')
    browser.close()
EOF
```

### 3. Identify selectors

For **server-rendered HTML** sites, use the `onboard_site.py` script:

```bash
PYTHONPATH=. python3 scripts/onboard_site.py
```

For **SPA** sites (Playwright), inspect the rendered DOM:

```bash
python3 << 'EOF'
from playwright.sync_api import sync_playwright
from lxml import html

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://example.com', wait_until='load', timeout=30000)
    page.wait_for_timeout(5000)
    doc = html.fromstring(page.content())

    # Find item containers
    for expr in [
        "//a[contains(@href,'/movie/')]/ancestor::div[contains(@class,'card')]",
        "//a[contains(@href,'/movie/')]/ancestor::div[contains(@class,'group')]",
        "//div[contains(@class,'media-card')]",
    ]:
        items = doc.xpath(expr)
        print(f'{expr}: {len(items)} items')

    # Inspect first item
    items = doc.xpath("//div[contains(@class,'media-card')]")
    if items:
        print(html.tostring(items[0], pretty_print=True).decode()[:1000])
    browser.close()
EOF
```

### 4. Add to `config/sites.yaml`

```yaml
  site-name:
    display_name: "Display Name"
    url: "https://example.com/"
    method: "playwright"  # http | cloudscraper | playwright
    required_content_marker_groups:
      - ["marker1", "marker2"]
    playwright_wait_selector: "a[href*='/movie/']"  # CSS selector for Playwright to wait on
    item_selector: "//div[contains(@class,'item-class')]"
    title_selector: "normalize-space(.//h3/text())"
    link_selector: "normalize-space(.//a/@href)"
    description_selector: "concat('<img src=\"', normalize-space(.//img/@src), '\" style=\"max-width:300px;max-height:450px;width:auto;height:auto;object-fit:contain;display:block;border-radius:4px;\">', '<br><a href=\"https://www.youtube.com/results?search_query=', normalize-space(.//h3/text()), '+preview%7Cpromo%7Ctrailer+-fake+-fan&sp=EgIYAQ%253D%253D\" target=\"_blank\" rel=\"noopener noreferrer\"><b style=\"color:#6600cc;\">Trailer</b></a>', '<br><a href=\"https://www.imdb.com/find?q=', normalize-space(.//h3/text()), '&s=tt\" target=\"_blank\" rel=\"noopener noreferrer\"><b style=\"color:#6600cc;\">IMDb</b></a>')"
    feed_file: "site-name.xml"
    category: "movies"  # movies | episodes | updates
    max_items: 24
    language: en  # ro | en
```

### 5. Generate feed

```bash
# Single site test
PYTHONPATH=. python3 << 'EOF'
import asyncio
from pathlib import Path
from core.config import load_config
from core.engine import GenerationEngine
from scraper.fetcher import Fetcher
from urllib.parse import urljoin
from core.feed import generate_rss

config = load_config(Path('config/sites.yaml'))
engine = GenerationEngine(config, Path('cache'), Path('feeds'))

async def test(site_name):
    site = next(s for s in config.sites if s.name == site_name)
    fetcher = Fetcher()
    items = await engine._extract_html_items(site, fetcher)
    print(f'{len(items)} items extracted')
    for item in items[:3]:
        print(f'  {item.title} -> {item.link}')
    await fetcher.close()
    for item in items:
        if item.link and not item.link.startswith('http'):
            item.link = urljoin(site.url, item.link)
    generate_rss(items, site_name=site.display_name or site.name,
                 site_url=site.url, category=site.category,
                 output_path=Path('feeds') / site.feed_file)

asyncio.run(test('site-name'))
EOF
```

### 6. Regenerate index

```bash
PYTHONPATH=. python3 scripts/generate_index.py
```

---

## SPA Scraping Strategies

### Strategy 1: Playwright rendered DOM (simplest)

Use `method: playwright` + `playwright_wait_selector`. Playwright renders JS, then XPath extracts from the live DOM. Works for:

- **Cineby** (`a[href*='/movie/'], a[href*='/tv/']`)
- **SpenFlix** (`a[href*='/movie/']`)
- **CinemaOS** (`a[href*='/movie/']`)

**Tradeoff**: Slow (~5-10s per site). Each Playwright launch = ~300MB RAM. Max 2 concurrent Playwright sessions.

### Strategy 2: Inline JSON extraction (faster)

Many Next.js sites embed movie data in the HTML. No browser needed.

**`__NEXT_DATA__`** (Next.js Pages Router):

```python
import json, re
match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
if match:
    data = json.loads(match.group(1))
    movies = data['props']['pageProps']['movies']
```

Found in: Cineby (101KB of movie/TV data)

**`__next_f.push`** (Next.js App Router / React Server Components):

```python
matches = re.findall(r'self\.__next_f\.push\(\[\d+,\"(.*?)\"\]\)', html, re.DOTALL)
```

Found in: SpenFlix, CinemaOS (raw data, harder to parse)

**Svelte inline JSON** (SvelteKit):

Found in: XPrime (trending/popular/TV arrays in script tag)

**Requires engine modification** to support — Playwright approach works without code changes.

### Strategy 3: API interception (best long-term)

Intercept XHR/fetch calls during Playwright session. Once API endpoints are known, scrape with direct HTTP calls instead of Playwright.

```python
# Discovery phase — run once per site
page.on("response", lambda resp: print(resp.url, resp.status))
page.goto(url)
# Look for /api/, /graphql, TMDB endpoints
```

### Strategy 4: WordPress REST API (auto-detect)

Engine automatically tries `/wp-json/wp/v2/posts?per_page=N&_embed=1` when HTML extraction fails.

---

## Fetch Methods

| Method | Best for | Speed | Anti-bot |
|--------|----------|-------|----------|
| `http` | Clean HTML sites | Fast | None |
| `cloudscraper` | Cloudflare Challenge v1 | Medium | Good |
| `playwright` | SPAs, Turnstile, tough Cloudflare | Slow | Best |

The fetcher chain: http → cloudscraper → playwright (automatic fallback).

---

## Site Configuration Reference

### Required fields

| Field | Description |
|-------|-------------|
| `name` | Unique alphanumeric key (used as YAML key) |
| `url` | Full page URL to scrape |
| `method` | Fetch method: `http`, `cloudscraper`, `playwright` |
| `item_selector` | XPath 2.0 expression to select each listing item |
| `title_selector` | XPath for item title (relative to item) |
| `link_selector` | XPath for item link (relative to item) |

### Optional fields

| Field | Default | Description |
|-------|---------|-------------|
| `display_name` | same as `name` | Human-readable name in the feed |
| `description_selector` | None | XPath to generate `<img>` + Trailer/IMDb links |
| `playwright_wait_selector` | None | CSS selector to wait for before reading DOM |
| `required_content_marker_groups` | None | OR-of-ANDs: `[["a","b"], ["c"]]` means `(a AND b) OR c` |
| `max_items` | None (all) | Max items per feed |
| `feed_file` | `{name}.xml` | Output filename |
| `category` | None | `movies`, `episodes`, `updates` |
| `language` | `ro` | `ro` or `en` |
| `fallback_urls` | [] | URLs to try if primary fails |
| `title_transform` | None | `title_case` for ALL-CAPS titles |
| `detail_title_selector` | None | XPath for per-item detail page title |
| `detail_description_selector` | None | XPath for per-item detail page description |

### XPath tips

- Use `encode-for-uri()` for URL-safe title encoding (elementpath 4.x supports it)
- Use `||` in selectors for fallback: `selector1 || selector2`
- Use `normalize-space()` to trim whitespace
- `./` paths are relative to each matched item
- Image URLs: prefer `@data-src` fallback to `@src` for lazy-loaded images

---

## Known Issues

- **f-hdonline.ro**: DNS fails from GitHub Actions IPs (NXDOMAIN). Old feed preserved.
- **sflix2.to, hdtoday.cc**: HTTP 521 (Cloudflare origin down) since Apr 2026.
- **dozaanimata.net**: Cloudflare Turnstile — unsolvable from CI IPs.
- **zfilmeonline.ro**: HTTP 403 from all methods in CI.
- **filmebro.com**: Geo-redirects to youku.tv from CI IPs.
- **`encode-for-uri` in description_selector**: May fail if item HTML contains SVG elements (breaks XML round-trip for elementpath). Remove `encode-for-uri()` to fix — browsers handle raw UTF-8 in query params.

---

## Performance & Cost Optimization

### Reduce Playwright usage

Playwright is the bottleneck (~10-15s per site). To minimize:

1. **Check `__NEXT_DATA__` first** — no browser needed
2. **Cache API endpoints** — after discovery, use direct HTTP
3. **Batch API scrapes** — use async HTTP instead of sequential Playwright
4. **Block unnecessary resources** in Playwright:
   ```python
   await page.route("**/*", lambda route: route.abort() 
       if route.request.resource_type() in ["image", "font", "media", "stylesheet"]
       else route.continue())
   ```

### GitHub Actions optimization

```yaml
# Cache Playwright browsers
- uses: actions/cache@v4
  with:
    path: ~/.cache/ms-playwright
    key: playwright-${{ runner.os }}
```

### Monitor feed health

```bash
# Check all feeds are valid XML
python3 -c "
import xml.etree.ElementTree as ET
from pathlib import Path
for f in sorted(Path('feeds').glob('*.xml')):
    try:
        tree = ET.parse(f)
        items = len(tree.findall('.//item'))
        print(f'  {f.name:35s} {items} items OK')
    except Exception as e:
        print(f'  {f.name:35s} FAIL: {e}')
"
```

---

## Extracting from SPA Inline JSON

### Next.js `__NEXT_DATA__` (Cineby pattern)

```python
import json, re

def extract_next_data(html: str) -> dict | None:
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>'
        r'(.*?)</script>', html, re.DOTALL
    )
    if match:
        return json.loads(match.group(1))
    return None
```

Data structure:
```python
{
  "props": {
    "pageProps": {
      "initialGenreMovies": [{"id", "title", "poster", "slug", "rating", "release_date", "mediaType"}, ...],
      "trendingSections": [{"name": "Trending", "movies": [...]}],
      "defaultSections": [{"name": "Popular", "movies": [...]}],
    }
  }
}
```

### Next.js `__next_f.push` (App Router)

```python
import re, json

def extract_next_f_push(html: str) -> list[dict]:
    items = []
    for match in re.finditer(
        r'"items":(\[.*?\])', html, re.DOTALL
    ):
        try:
            items_str = match.group(1)
            # Unescape JSON string
            items_str = items_str.replace('\\"', '"').replace('\\n', '')
            items.extend(json.loads(items_str))
        except json.JSONDecodeError:
            continue
    return items
```

Data structure:
```python
[{"id", "title", "posterPath", "mediaType", "overview", "releaseDate", "voteAverage"}, ...]
```

### SvelteKit inline data (XPrime pattern)

```python
import re, json

def extract_svelte_data(html: str) -> dict | None:
    match = re.search(
        r'data:\s*(\{.*?"trending".*?\})', html, re.DOTALL
    )
    if match:
        return json.loads(match.group(1))
    return None
```

---

## Testing New Sites — Full Protocol

### Batch test all candidates

```python
import httpx, time

CANDIDATES = [
    ("Name", "https://example.com/"),
]

for name, url in CANDIDATES:
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15,
                      headers={"User-Agent": "Mozilla/5.0"})
        print(f"{name:20s} {r.status_code} ({len(r.content)}b)")
        # Check for markers
        has_poster = 'poster' in r.text.lower()
        has_card = 'card' in r.text.lower()
        has_next_data = '__NEXT_DATA__' in r.text
        print(f"  poster={has_poster} card={has_card} __NEXT_DATA__={has_next_data}")
    except Exception as e:
        print(f"{name:20s} FAIL: {e}")
```

### Deep test with cloudscraper

```python
import cloudscraper
s = cloudscraper.create_scraper()
r = s.get(url, timeout=12)
```

### Deep test with Playwright

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="load", timeout=30000)
    page.wait_for_timeout(5000)
    content = page.content()
    print(f"Rendered: {len(content)}b")
    items = page.query_selector_all('a[href*=\"/movie/\"], a[href*=\"/tv/\"]')
    print(f"Movie/TV links: {len(items)}")
    browser.close()
```

### Test alternate paths

```python
PATHS = ["/", "/movies", "/tv", "/home", "/browse",
         "/feed/", "/wp-json/wp/v2/posts?per_page=10",
         "/api/movies", "/api/v1/movies"]
for path in PATHS:
    r = httpx.get(f"https://example.com{path}", ...)
```

---

## Tools Evaluation

| Tool | Purpose | Installed? |
|------|---------|------------|
| Playwright | Browser automation for SPAs | Yes |
| cloudscraper | Cloudflare bypass | Yes |
| httpx | HTTP client | Yes |
| lxml | HTML/XML parsing | Yes |
| elementpath | XPath 2.0 for Python | Yes |
| Crawlee | Web scraping framework | No |
| Cheerio | Node.js HTML parser | No (Python project) |

### Install additional tools if needed

```bash
# Crawlee (Node.js) for advanced crawling
npm install -g crawlee playwright

# Playwright browsers cache
python3 -m playwright install chromium

# Browserless CLI for debugging
curl -X POST https://chrome.browserless.io/content \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

---

## Deployment (GitHub Actions)

The `generate_feeds.yml` workflow:

1. Runs on cron schedule
2. Installs Python + Playwright
3. Generates all feeds
4. Commits updated feeds
5. GitHub Pages serves `index.html` + `feeds/`

### Secrets

- `RSS_GENERATOR_PROXY_URL` — optional proxy for fetcher

---

## Site Status Legend

| Status | Meaning |
|--------|---------|
| Active | Working feed, updated every run |
| DNS Fail | Domain not resolving from CI IPs |
| HTTP 403/521 | Blocked by WAF/Cloudflare |
| Timeout | No response within 60s |
| Empty | No items extracted (SPA without Playwright config) |
