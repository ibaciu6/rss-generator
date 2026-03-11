## RSS Generator

**Generic, config‑driven RSS & Atom feed generator for arbitrary websites.**

This project provides a modular scraping and feed generation platform similar in spirit to PolitePol. It can:

- **Scrape arbitrary websites** using multiple strategies (HTTP, Cloudflare bypass, headless browser).
- **Parse HTML** using configurable XPath / CSS selectors.
- **Generate RSS 2.0 and Atom feeds** using `feedgen`.
- **Run on a schedule via GitHub Actions** and publish feeds through GitHub Pages.

### Features

- **Config‑driven sites**: define sites in `config/sites.yaml`.
- **Multiple fetch strategies**: `httpx`, `cloudscraper`, and Playwright with automatic fallback.
- **HTML parsing**: `lxml` and `BeautifulSoup` with flexible selectors.
- **Deduplication**: cache of seen URLs in `data/cache.json`.
- **CLI tool**: `rss-generator generate` to run all configured sites.
- **GitHub Actions automation**: scheduled feed regeneration and commit.
- **Docker support**: containerized environment with Playwright.

### Quick start (local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# First time only – install Playwright browsers
playwright install chromium

# Generate feeds
python -m core.cli generate
```

Detailed usage, architecture, and development notes live in `docs/`.

