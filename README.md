<div align="center">
  <h1>📡 RSS Generator</h1>
  <p><strong>Config‑driven RSS feeds for streaming sites.</strong> Scrapes Romanian + English streaming sites, enriches with TMDb posters, and publishes to GitHub Pages — <a href="https://ibaciu6.github.io/rss-generator">live index</a>.</p>
  <p>Inspired by Feed43 (pattern-based HTML-to-RSS) and PolitePaul (XPath-driven visual feed builder).</p>

  <a href="https://ibaciu6.github.io/rss-generator"><img src="https://img.shields.io/badge/status-active-brightgreen?style=flat-square"></a>
  <a href="https://github.com/ibaciu6/rss-generator/actions"><img src="https://img.shields.io/github/actions/workflow/status/ibaciu6/rss-generator/update.yml?style=flat-square"></a>
  <a href="https://github.com/ibaciu6/rss-generator/blob/main/LICENSE"><img src="https://img.shields.io/github/license/ibaciu6/rss-generator?style=flat-square"></a>
</div>

---

## Published feeds

All feeds organized on the [index page](https://ibaciu6.github.io/rss-generator):

| Category | Languages |
|----------|-----------|
| Movies | RO + EN |
| Episodes | RO |

[<kbd> 📥 Download OPML </kbd>](https://raw.githubusercontent.com/ibaciu6/rss-generator/main/feeds.opml) — import into Inoreader or any RSS reader, pre‑sorted into folders.

Feeds are enriched with TMDb posters, years, IMDb links, and YouTube trailer links. Sites failing 3 consecutive runs auto‑skip until recovery.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Install Playwright browser
python -m playwright install chromium

# Full pipeline
PYTHONPATH=. python scripts/generate_feeds.py
PYTHONPATH=. python scripts/enrich_posters.py
PYTHONPATH=. python scripts/fix_feeds.py
PYTHONPATH=. python scripts/generate_index.py

# Serve locally (static files only)
./scripts/serve.sh
```

---

## Features

- **Streaming‑site feeds** — scraped via HTTP, cloudscraper, and Playwright
- **Category‑based filtering** — block unwanted content (e.g. erotic) by WordPress category class
- **Multi‑page scraping** — paginate through multiple pages for larger item pools
- **Poster, IMDb & trailer enrichment** — TMDb API with rate‑limiting; falls back to title‑based search; auto‑injects IMDb and YouTube trailer search links
- **Full catalog** — includes future‑dated (unreleased) movies; no removal pass
- **Live status page** at `/` — per‑feed health, last update, item count
- **One‑click OPML** — bulk import into Inoreader with folder structure
- **Auto‑skip** — dead sites (3 consecutive failures) silently skipped; resets on recovery
- **Config‑driven** — all sites declared in `config/sites.yaml`; no code changes needed
- **GitHub Actions** — hourly cron + push‑triggered rebuilds, auto‑deployed to Pages
- **WebSub hub** — real‑time feed update notifications to aggregators

---

## Architecture

```
GitHub Actions (cron @:19 hourly)
  └─ GenerationEngine
       ├─ Fetcher (http → cloudscraper → Playwright fallback)
       ├─ Parser (elementpath XPath 2.0 → lxml XPath 1.0)
       │   └─ category_selector — extract page‑level category classes
       ├─ DedupStore (URL‑based dedup, 500 URLs/site cap)
       ├─ Feed writer (feedgen — RSS 2.0)
       ├─ _fetch_extra_pages — multi‑page pagination
       └─ _filter_items — blocked_categories + title_filter_patterns
```

- **`config/sites.yaml`** — master site list (XPath selectors, fetch method, category, blocked_categories, pages)
- **`core/config.py`** — SiteConfig dataclass: `category_selector`, `blocked_categories`, `title_filter_patterns`, `pages`
- **`core/engine.py`** — orchestration: shuffle, stagger, timeout (240s), concurrent (6 sites), multi‑page fetch
- **`scraper/fetcher.py`** — 3‑strategy fetch chain with anti‑detection and browser challenge retry
- **`scraper/parser.py`** — XPath 2.0 parsing via elementpath, falls back to lxml XPath 1.0; category extraction
- **`core/feed.py`** — RSS 2.0 generation with WebSub hub, syndication tags, TMDb poster sizing
- **`core/tmdb.py`** — rate‑limited TMDb API client with in‑memory cache; `search_movie(title)` fallback
- **`scripts/enrich_posters.py`** — TMDb ID lookup, title‑based year search, poster enrichment; cleans torrent‑style titles for TMDB search; injects IMDb + YouTube trailer search links
- **`scripts/fix_feeds.py`** — post‑processing: year formatting, watch‑link appends, poster style

---

## Adding or removing a site

Edit `config/sites.yaml`, then run `PYTHONPATH=. python scripts/generate_index.py` to rebuild `index.html` and `feeds.opml` automatically.

For new sites, use the interactive onboarding:

```bash
PYTHONPATH=. python scripts/onboard_site.py
```

---

## GitHub Actions

[`.github/workflows/update.yml`](.github/workflows/update.yml) — runs hourly + on push + manual dispatch:

1. Install Python + Playwright (cached)
2. Generate all feeds
3. Enrich with TMDb posters, years, IMDb links, and trailer links
4. Post‑process (Next.js image URLs, watch‑link appends, poster normalization)
5. Rebuild `index.html` + `feeds.opml`
6. Commit & push changes (rebase on conflict, `--theirs` for feeds)
7. Deploy to GitHub Pages
8. Ping WebSub hub for real‑time updates

**Secrets needed:** `TMDB_API_KEY`, `RSS_GENERATOR_PROXY_URL`

Enable **Settings → Actions → General → Workflow permissions: Read and write** and set **Settings → Pages → Source** to **GitHub Actions**.

---

## Security

Automated secret scanning runs on every push, PR, manual dispatch, and daily schedule via [`.github/workflows/secret-scan.yml`](.github/workflows/secret-scan.yml). See [`SECURITY.md`](SECURITY.md) for reporting.

---

> Detailed architecture and development reference: [`PROJECT.md`](PROJECT.md)
