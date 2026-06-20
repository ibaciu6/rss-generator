## Research & Inspiration

### RSSHub (`DIYgod/RSSHub`)

- **Highlights**:
  - Large collection of route definitions mapping sites to feeds.
  - Modular architecture: each site has its own route file.
  - Uses Node.js and a combination of HTTP fetch + headless browser when needed.
  - Caching and rate limiting per route.
- **Ideas adopted**:
  - Config / route‑driven design for adding new feeds without touching core logic.
  - Multiple fetch strategies with fallbacks.
  - Clear separation between fetching, parsing, and feed generation.

### rss-proxy (`damoeb/rss-proxy`)

- **Highlights**:
  - Converts arbitrary websites to RSS via CSS selectors.
  - Exposes a web UI for defining new feeds.
  - Stores configuration for each site.
- **Ideas adopted**:
  - Configurable selectors: `item_selector`, `title_selector`, `link_selector`, etc.
  - YAML‑backed configuration to keep things Git‑friendly.
  - Simple mental model: "HTML in, RSS out" with minimal assumptions.

### Scraperr (`jaypyles/Scraperr`)

- **Highlights**:
  - Generic scraping framework with pluggable pipelines.
  - Focus on reusability and composable scraping steps.
- **Ideas adopted**:
  - Separation between scraper engine and parsing engine.
  - Use of structured objects for scraped items.

### crawler-buddy (`rumca-js/crawler-buddy`)

- **Highlights**:
  - Crawler abstraction with multiple strategies.
  - Emphasis on reliability and resilience.
- **Ideas adopted**:
  - Retry strategies with exponential backoff.
  - Clear logging for failures and fallbacks.

### Design conclusions

- Use **Python 3.11+** with `httpx`, `cloudscraper`, and Playwright for fetching.
- Use `lxml` + `BeautifulSoup` for flexible HTML parsing.
- Use `feedgen` to generate valid RSS 2.0 and Atom feeds.
- Make the system **config‑driven** with `config/sites.yaml`.
- Support **multiple fetch strategies** and automatic fallback.
- Organize code into clear modules:
  - config engine
  - scraper engine
  - parser engine
  - dedup engine
  - feed generator
  - automation (CLI + GitHub Actions)

