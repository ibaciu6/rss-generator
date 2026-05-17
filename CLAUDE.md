# RSS Generator

Python RSS feed generator that scrapes 49 streaming sites (RO + EN) via GitHub Actions cron, publishes to GitHub Pages.

## Quick Start
```bash
PYTHONPATH=. python3 scripts/generate_feeds.py   # generate all feeds
PYTHONPATH=. python3 scripts/enrich_posters.py    # TMDB poster enrichment
PYTHONPATH=. python3 scripts/fix_feeds.py         # post-process (watch links, etc)
PYTHONPATH=. python3 scripts/generate_index.py   # rebuild index.html + feeds.opml
PYTHONPATH=. python3 -m pytest tests/            # run tests
```

## Key Files
- `config/sites.yaml` — 49 site configs (url, method, xpath selectors)
- `core/engine.py` — GenerationEngine (parallel scrape, fallback chain)
- `core/config.py` — SiteConfig/Config dataclasses + YAML loader
- `core/feed.py` — RSS 2.0 generation via feedgen
- `scraper/fetcher.py` — Fetcher (http → cloudscraper → playwright fallback)
- `scraper/parser.py` — Parser (elementpath XPath 2.0 → lxml XPath 1.0)
- `core/dedup.py` — URL dedup with 500 URLs/site cap
- `core/tmdb.py` — TMDB poster enrichment (rate-limited)
- `scripts/fix_feeds.py` — Post-processing: watch-link appends, poster style, year formatting
- `scripts/enrich_posters.py` — TMDB poster/year extraction

## URL Style
Feeds link to movie/show detail pages, not direct players. No `/watch/` or `/player/` suffix rewriting.

## CI
`.github/workflows/update.yml` runs hourly @:19 UTC + on push to main.
Steps: generate_feeds → enrich_posters → fix_feeds → generate_index → commit → deploy Pages.
Secrets: TMDB_API_KEY (required), RSS_GENERATOR_PROXY_URL (optional).
