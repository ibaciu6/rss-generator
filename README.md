## RSS Generator

**Generic, config‑driven RSS & Atom feed generator for arbitrary websites.**

This project provides a modular scraping and feed generation platform similar in spirit to PolitePol. It can:

- **Scrape arbitrary websites** using multiple strategies (HTTP, Cloudflare bypass, headless browser).
- **Parse HTML** using configurable XPath / CSS selectors.
- **Generate RSS 2.0 and Atom feeds** using `feedgen`.
- **Run on a schedule via GitHub Actions** and publish feeds through GitHub Pages.

### Features

- **Config‑driven sites**: one YAML per source under `config/sites/movies/` or `config/sites/series/` (see each file’s `schedule` cron).
- **Multiple fetch strategies**: `httpx`, `cloudscraper`, and Playwright with automatic fallback.
- **Fallback and validation**: alternate URLs, allowed/blocked final hosts, and challenge-page detection.
- **HTML parsing**: `lxml` and `BeautifulSoup` with flexible selectors.
- **Deduplication**: per-site cache files under `data/cache/<site>.json` (or `data/cache.json` when generating all sites locally).
- **CLI tool**: `rss-generator generate` (all sites) or `generate --site <id>` for one feed.
- **GitHub Actions**: one workflow per site (`.github/workflows/site-*.yml`) on its own schedule; Pages deploy when `feeds/**` or `index.html` changes.
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

The onboarding flow tries the existing fetch methods, proposes preview feeds based on repeated page content, writes `config/sites/movies/<slug>.yaml` or `config/sites/series/<slug>.yaml` from category, re-renders per-site workflows, commits, pushes, and dispatches the matching `site-<slug>.yml` workflow.

### Schedule tuning (probe + stagger)

`PYTHONPATH=. python scripts/suggest_site_schedules.py` probes each listing URL (HEAD, optional GET fallback, same-host `robots.txt`) and prints a recommended `minute */N * * *` cron with a different minute per site. Episode-style feeds bias toward a smaller `N` than movie listings; slow responses, HTTP errors, long `max-age`, and `Crawl-delay` push `N` upward. Pass `--write` to rewrite `schedule:` in each site YAML and regenerate `.github/workflows/site-*.yml`. Use `--reshuffle` to draw new random minutes every run; combine with `--seed` for reproducibility.

### GitHub Actions

- **Per-site feeds**: [`.github/workflows/site-<name>.yml`](.github/workflows/) — each runs on its own `schedule` (from the site YAML), on `workflow_dispatch`, and when shared Python/config deps change.
- **Pages**: [`.github/workflows/deploy-pages.yml`](.github/workflows/deploy-pages.yml) — on pushes that touch `feeds/**` or `index.html`, builds the site and deploys to GitHub Pages.
- **Index + workflow regen**: [`.github/workflows/regenerate-sources.yml`](.github/workflows/regenerate-sources.yml) — when `config/sites/**` or the generator scripts change, rebuilds the static `index.html` and all `site-*.yml` files.

Enable **Settings → Actions → General → Workflow permissions: Read and write** and set **Settings → Pages → Source** to **GitHub Actions**. For strict geo blocks, add **`RSS_GENERATOR_PROXY_URL`** as a repository secret (same format as the local env var).

### Security

- Automated secret scanning runs on pushes, pull requests, manual dispatch, and a daily schedule via [`.github/workflows/secret-scan.yml`](.github/workflows/secret-scan.yml).
- Security reporting guidance lives in [`SECURITY.md`](SECURITY.md).

Detailed usage, architecture, and development notes live in `docs/`.
