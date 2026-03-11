## Architecture

### Overview

The RSS generator is organized into modular components:

- **Config engine** (`core.config`): loads site definitions from `config/sites.yaml`.
- **Scraper engine** (`scraper.fetcher`): fetches HTML using multiple strategies.
- **Parser engine** (`scraper.parser`): extracts items using XPath selectors.
- **Dedup engine** (`core.dedup`): keeps track of seen URLs per site.
- **Feed generator** (`core.feed`): generates RSS 2.0 and Atom feeds.
- **Automation engine** (`core.engine`, `core.cli`): wires everything together and exposes a CLI.

### Data flow

1. `core.cli` loads configuration and constructs a `GenerationEngine`.
2. `GenerationEngine`:
   - loads the dedup cache from `data/cache.json`
   - runs scraping for all sites in parallel using `anyio` task groups
   - calls `Fetcher` to retrieve HTML content
   - calls `Parser` to extract items
   - filters out already seen URLs via `DedupStore`
   - calls `generate_rss_and_atom` to write feeds to `feeds/`
3. The dedup cache is written back to `data/cache.json`.

### Scraping strategies

- **http** (default): `httpx` async client with retries and redirects.
- **cloudscraper**: Cloudflare‑aware HTTP client, used as a fallback or primary method.
- **playwright**: headless Chromium via Playwright for heavy client‑side rendering / Cloudflare.

The strategy chain is built as:

- `http`: http → cloudscraper → Playwright
- `cloudscraper`: cloudscraper → http → Playwright
- `playwright`: Playwright → http → cloudscraper

Each step has retries with exponential backoff.

### Parsing

- HTML is parsed with `lxml.html`.
- Site configuration provides XPath selectors:
  - `item_selector`
  - `title_selector`
  - `link_selector`
  - `description_selector`
  - `date_selector`
- Parsed results are represented as `ParsedItem` dataclasses.

### Feed generation

- `feedgen` is used to produce:
  - RSS 2.0 feed at `feeds/<feed_file>`
  - Atom feed at `feeds/<feed_file>.atom.xml`
- Each item includes:
  - title
  - link
  - description (optional)
  - guid (link)
  - pubDate / updated
  - category (from site config)

