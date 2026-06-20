# RSS Feed Generator

Scrapes ~49 streaming sites (RO/EN) → RSS/Atom feeds with posters.

## Quick Start

```bash
./start.sh              # interactive menu
PYTHONPATH=. python3 scripts/generate_feeds.py   # headless
```

## Files

| File | Purpose |
|------|---------|
| `config/sites.yaml` | All 49 sites with XPath selectors, fetch method, filters |
| `core/engine.py` | Parallel scraping with fallback chains (http→cloudscraper→playwright) |
| `core/feed.py` | RSS 2.0/Atom feed generation via feedgen |
| `core/dedup.py` | URL-based dedup (500 URLs/site cache) |
| `core/tmdb.py` | TMDB poster enrichment (rate-limited) |
| `core/cli.py` | CLI: `generate`, `onboard-site` commands |
| `scripts/generate_feeds.py` | Entry: scrape all sites |
| `scripts/enrich_posters.py` | Fetch TMDB posters |
| `scripts/fix_feeds.py` | Post-process: watch links, poster styling |
| `scripts/generate_index.py` | Rebuild index.html + feeds.opml |
| `scripts/onboard_site.py` | Interactive site onboarding |
| `tests/` | Pytest test suite |

## Config (`config/sites.yaml`)

Each site needs: `name`, `url`, `method` (http/cloudscraper/playwright), XPath selectors, `feed_file`, `category`, `language`, `max_items`.

## Env Vars

- `TMDB_API_KEY` — required for poster enrichment
- `RSS_GENERATOR_PROXY_URL` — optional proxy

## Install

```bash
pip install -r requirements.txt
playwright install chromium --with-deps
```

## Tech

Python 3.11+, requests/httpx/cloudscraper, lxml+elementpath, feedgen, pyyaml, playwright. GitHub Actions runs hourly (UTC :19).
