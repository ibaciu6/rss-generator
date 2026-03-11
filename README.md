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

### Published feeds (GitHub Pages)

Once GitHub Pages is enabled (Settings → Pages → Source: branch `main`, folder `/feeds`), feeds are available at:

| Feed        | RSS 2.0 | Atom |
|-------------|---------|------|
| Hacker News | [hackernews.xml](https://ibaciu6.github.io/rss-generator/hackernews.xml) | [hackernews.atom.xml](https://ibaciu6.github.io/rss-generator/hackernews.atom.xml) |
| Sitefilme   | [sitefilme.xml](https://ibaciu6.github.io/rss-generator/sitefilme.xml)   | [sitefilme.atom.xml](https://ibaciu6.github.io/rss-generator/sitefilme.atom.xml)   |

Add these URLs to Inoreader or any RSS reader.

### Quick start (local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# First time only – install Playwright browsers
playwright install chromium

# Generate feeds (from repo root)
PYTHONPATH=. python scripts/generate_feeds.py
# or
PYTHONPATH=. python -m core.cli generate
```

### GitHub Actions

The workflow [`.github/workflows/update.yml`](.github/workflows/update.yml) runs every 2 hours and on manual trigger (`workflow_dispatch`). It installs dependencies, generates feeds, and commits updated `feeds/*.xml` back to the repo. Enable **Settings → Actions → General → Workflow permissions: Read and write**.

Detailed usage, architecture, and development notes live in `docs/`.

