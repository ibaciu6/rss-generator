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
- **Fallback and validation**: alternate URLs, allowed/blocked final hosts, and challenge-page detection.
- **HTML parsing**: `lxml` and `BeautifulSoup` with flexible selectors.
- **Deduplication**: cache of seen URLs in `data/cache.json`.
- **CLI tool**: `rss-generator generate` to run all configured sites.
- **GitHub Actions automation**: scheduled feed regeneration and commit.
- **Docker support**: containerized environment with Playwright.

### Published feeds (GitHub Pages)

Once GitHub Pages is enabled with **Settings → Pages → Source: GitHub Actions**, feeds are available at:

- [filmehd-filme.xml](https://ibaciu6.github.io/rss-generator/feeds/filmehd-filme.xml)
- [filmehd-seriale.xml](https://ibaciu6.github.io/rss-generator/feeds/filmehd-seriale.xml)
- [portalultautv.xml](https://ibaciu6.github.io/rss-generator/feeds/portalultautv.xml)
- [seriale-online-episodes.xml](https://ibaciu6.github.io/rss-generator/feeds/seriale-online-episodes.xml)
- [seriale-online-movies.xml](https://ibaciu6.github.io/rss-generator/feeds/seriale-online-movies.xml)
- [fsonline-episoade.xml](https://ibaciu6.github.io/rss-generator/feeds/fsonline-episoade.xml)
- [fsonline-film.xml](https://ibaciu6.github.io/rss-generator/feeds/fsonline-film.xml)
- [filmflix.xml](https://ibaciu6.github.io/rss-generator/feeds/filmflix.xml)
- [filmehd-cc-filme.xml](https://ibaciu6.github.io/rss-generator/feeds/filmehd-cc-filme.xml)
- [filmehd-cc-seriale.xml](https://ibaciu6.github.io/rss-generator/feeds/filmehd-cc-seriale.xml)
- [f-hdonline.xml](https://ibaciu6.github.io/rss-generator/feeds/f-hdonline.xml)
- [voxfilmeonline.xml](https://ibaciu6.github.io/rss-generator/feeds/voxfilmeonline.xml)

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

# Interactively discover a new site, preview feed styles, write config,
# commit/push it, and trigger the update workflow
PYTHONPATH=. python -m core.cli onboard-site https://example.com/
# or
PYTHONPATH=. python scripts/onboard_site.py https://example.com/

# Optional: use a geo/residential proxy for blocked sites
export RSS_GENERATOR_PROXY_URL="http://user:pass@host:port"
```

The onboarding flow tries the existing fetch methods, proposes preview feeds based on repeated page content, writes the selected selectors to `config/sites.yaml`, and can dispatch `Update RSS and Deploy Pages` after pushing the config change.

### GitHub Actions

The workflow [`.github/workflows/update.yml`](.github/workflows/update.yml) runs every 30 minutes at minutes 7 and 37 UTC, on pushes to `main`, and on manual trigger (`workflow_dispatch`). It installs dependencies, generates feeds, commits updated `feeds/*.xml` back to the repo, then uploads the published site to GitHub Pages. Enable **Settings → Actions → General → Workflow permissions: Read and write** and set **Settings → Pages → Source** to **GitHub Actions**. For sites behind strict geo rules, add a repository secret **`RSS_GENERATOR_PROXY_URL`** (same format as the local env var); the workflow passes it to the generator so `httpx`, cloudscraper, and Playwright can route through your proxy.

### Security

- Automated secret scanning runs on pushes, pull requests, manual dispatch, and a daily schedule via [`.github/workflows/secret-scan.yml`](.github/workflows/secret-scan.yml).
- Security reporting guidance lives in [`SECURITY.md`](SECURITY.md).

Detailed usage, architecture, and development notes live in `docs/`.
