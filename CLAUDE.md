# RSS Generator — Project Rules

## Keep derived files in sync with config/sites.yaml

Whenever you add or remove a site entry in `config/sites.yaml`, you **must** update all of the following files to match:

1. **`feeds.opml`** — Add or remove the corresponding `<outline>` element inside the correct group.
2. **`index.html`** — Add or remove the corresponding `<tr>` row in the status table.
3. **`feeds/<feed_file>.xml`** — When **removing** a site, delete the generated feed XML file. When **adding** a site, the next workflow run generates it.

Do not commit a `config/sites.yaml` change unless the other three files are consistent.

## References

- **Config**: `config/sites.yaml` — master list of all sites with selectors, method, category, etc.
- **Feeds OPML**: `feeds.opml` — OPML index for Inoreader import.
- **Index page**: `index.html` — status table of all feeds.
- **Generated feeds**: `feeds/*.xml` — per-site RSS output.
- **Workflow**: `.github/workflows/update.yml` — CI/CD: generate, enrich, deploy to Pages.
- **Core engine**: `core/engine.py`, `core/config.py`, `core/cli.py`.

## Overview

Python RSS feed generator that scrapes streaming sites (Romanian + English) via GitHub Actions cron, publishing feeds to GitHub Pages.

```
GitHub Actions (cron @:19 hourly)
  └─ GenerationEngine
       ├─ Fetcher (http → cloudscraper → Playwright fallback)
       ├─ Parser (elementpath XPath 2.0 → lxml XPath 1.0)
       ├─ DedupStore (URL-based dedup, 500 URLs/site cap)
       ├─ RSS Feed writer (feedgen)
       └─ WordPress API reader (fallback)
```

## Key Config Fields (config/sites.yaml)

| Field | Required | Description |
|-------|----------|-------------|
| `name` (YAML key) | yes | Unique slug, e.g. `f_hdonline` |
| `url` | yes | Page URL to scrape |
| `method` | yes | `http` / `cloudscraper` / `playwright` |
| `item_selector` | yes | XPath 2.0 to select each listing item |
| `title_selector` | yes | XPath for item title (relative to item) |
| `link_selector` | yes | XPath for item link (relative to item) |
| `description_selector` | no | XPath to generate `<img>` + Trailer/IMDb links |
| `feed_file` | no | Output filename (default: `{name}.xml`) |
| `category` | no | `movies` / `episodes` / `tvshows` / `updates` |
| `max_items` | no | Max items per feed (default: all) |
| `language` | no | `ro` or `en` (default: `ro`) |
| `playwright_wait_selector` | no | CSS selector for Playwright to wait on |
| `required_content_marker_groups` | no | OR-of-ANDs: `[["a","b"], ["c"]]` means `(a AND b) OR c` |
| `title_transform` | no | `title_case` for ALL-CAPS titles |
| `detail_title_selector` | no | XPath for per-item detail page title |
| `detail_description_selector` | no | XPath for per-item detail page description |

## Core Modules

### `core/config.py` — `SiteConfig` dataclass + `load_config(path)`
Parses `sites.yaml` into a list of `SiteConfig` objects. Validates fetch method.

### `core/engine.py` — `GenerationEngine`
Orchestrates scraping. Key behavior:
- Shuffles sites each run for randomized request order
- Max 6 concurrent sites, 240s per-site timeout
- Tracks consecutive failures in `data/skipped.json` (auto-skips after 3)
- Fallback chain: HTML scrape → native RSS → WordPress REST API
- For HTML: tries all fetch methods in order, with/without listing marker validation
- Preserves old healthy feed on failure (only writes failure feed if previous was also failure)
- `_enrich_items()`: fetches detail pages for missing titles/descriptions

### `scraper/fetcher.py` — `Fetcher`
Multi-strategy fetcher:
- `_fetch_http`: httpx with random User-Agent, 0.5-2s jitter, 3 retries (exp backoff)
- `_fetch_cloudscraper`: Cloudflare bypass via thread, 0.5-3s jitter, 3 retries
- `_fetch_playwright`: Chromium via thread, 1-4s jitter, 2 retries, 25-90s nav timeout
  - Anti-detection: spoofs webdriver, languages, plugins, chrome.runtime
  - Retries on browser challenge pages (up to 4 times with 5s waits)
  - `playwright_scroll_to`: scrolls container or `window` to trigger lazy-load
  - **Concurrency**: limited to 2 concurrent Playwright slots (GitHub runner RAM)
- Strategy chain depends on `method` config (configured method first, then fallbacks)
- Blocked content detection (CF challenge, 521, etc.) triggers next strategy
- **Env**: `RSS_GENERATOR_PROXY_URL` for optional proxy

### `scraper/parser.py` — `Parser`
- Parses HTML via lxml, supports XPath 2.0 via elementpath (falls back to lxml XPath 1.0)
- Handles `||` in selectors as fallback chain
- `parse_rss_items()`: parses native RSS XML
- `parse_wordpress_posts()`: parses WordPress REST API JSON (requires `_embed=1`)
- Date parsing: ISO 8601 → common formats → dateutil fallback

### `core/feed.py` — RSS generation
- Uses `feedgen` library for RSS 2.0 output
- Post-processes with `_decorate_rss_file()`: WebSub hub link, syndication tags, TTL
- TMDB poster downscaling (w342), fixed 300px width styling
- Failure feeds: preserves existing healthy feed; only overwrites if previous was also a failure
- **Env**: `RSS_FEED_PUBLIC_BASE` for absolute self-link URLs (set in CI)

### `core/dedup.py` — `DedupStore`
URL-based dedup across runs. Persisted to `data/cache.json`. Caps at 500 URLs/site.

### `core/tmdb.py` — TMDb poster enrichment
- Rate-limited (0.25s between requests), in-memory cached
- `movie_lookup(tmdb_id)`, `tv_lookup(tmdb_id)`, `find_by_imdb(imdb_id)`
- **Env**: `TMDB_API_KEY`

### `core/logging_utils.py` — structlog JSON logging

### `core/onboarding.py` — Interactive site discovery
- `run_onboarding()` CLI flow: fetch → discover selectors → preview → write config → commit → dispatch workflow
- `discover_preview_options()`: tries all fetch methods, auto-discovers item selectors
- Auto-generates description selectors with poster + Trailer/IMDb links

### `core/cli.py` — CLI entry point
```
rss-generator generate [--config path] [--cache path] [--feeds-dir path]
rss-generator onboard-site [url] [--config path] [--no-push] [--no-dispatch]
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/generate_feeds.py` | Entry point for CI (calls `cli.py main(["generate"])`) |
| `scripts/generate_index.py` | Rebuilds `index.html` + `feeds.opml` from feed XML files |
| `scripts/enrich_posters.py` | TMDb poster/year enrichment by extracting IDs from links |
| `scripts/fix_feeds.py` | Post-processing: Next.js image URLs, watch-link appends, title year formatting, poster style normalization |
| `scripts/onboard_site.py` | Interactive site onboarding helper |
| `scripts/test_sites.py` / `test_sites_deep.py` | Batch site testing |

## GitHub Actions Workflow (update.yml)

Pipeline:
1. Checkout → Python setup → cache Playwright → install deps
2. Random 0-60s delay (for scheduled runs to avoid predictable timing)
3. `generate_feeds.py` → `enrich_posters.py` → `fix_feeds.py`
4. Collect failures → `generate_index.py` (rebuilds index.html + feeds.opml)
5. Commit + push changes (with rebase on conflict, `--theirs` for feeds/*)
6. Prepare Pages artifact: `index.html` + `feeds.opml` + `feeds/` + `.nojekyll`
7. Deploy to Pages → Ping WebSub hub for real-time feed updates

**Secrets needed**: `TMDB_API_KEY`, `RSS_GENERATOR_PROXY_URL`

## Adding a Site

1. Identify type: server-rendered HTML / WordPress / SPA (Playwright) / native RSS
2. Test fetch methods (`httpx` → `cloudscraper` → `playwright`)
3. Use `PYTHONPATH=. python3 scripts/onboard_site.py` for interactive discovery
4. Or manually add entry to `config/sites.yaml`
5. Update `feeds.opml` + `index.html` (or run `PYTHONPATH=. python3 scripts/generate_index.py`)
6. Single-site test: `PYTHONPATH=. python3` with `GenerationEngine._extract_html_items()` snippet

## Common Issues

- **SPA sites** (Next.js, etc.): Need `method: playwright` + `playwright_wait_selector`
- **encode-for-uri in description_selector**: May fail if item HTML has SVG elements. Remove `encode-for-uri()` to fix (browsers handle raw UTF-8 in query params).
- **CF Turnstile**: Unsolvable from CI IPs (mark as unavailable)
- **Geo-redirects**: Some sites redirect to different domains based on CI IP geolocation
- **GitHub runner RAM**: Only 2 concurrent Playwright sessions (Chromium ~300MB each)

## Testing

```bash
# Run tests
PYTHONPATH=. python3 -m pytest tests/

# Validate all feeds
python3 -c "
import xml.etree.ElementTree as ET
from pathlib import Path
for f in sorted(Path('feeds').glob('*.xml')):
    try:
        tree = ET.parse(f)
        items = len(tree.findall('.//item'))
        print(f'{f.name:35s} {items} items OK')
    except Exception as e:
        print(f'{f.name:35s} FAIL: {e}')
"

# Test a single site
PYTHONPATH=. python3 scripts/test_sites.py
```
