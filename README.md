<div align="center">
  <h1>📡 RSS Generator</h1>
  <p><strong>Config‑driven RSS feeds for streaming sites.</strong> Scrapes 49 Romanian + English sources, enriches with TMDb posters, and publishes to GitHub Pages — <a href="https://ibaciu6.github.io/rss-generator">live index</a></p>

  <a href="https://ibaciu6.github.io/rss-generator"><img src="https://img.shields.io/badge/status-49%20feeds%20online-brightgreen?style=flat-square"></a>
  <a href="https://github.com/ibaciu6/rss-generator/actions"><img src="https://img.shields.io/github/actions/workflow/status/ibaciu6/rss-generator/update.yml?style=flat-square"></a>
  <a href="https://github.com/ibaciu6/rss-generator/blob/main/LICENSE"><img src="https://img.shields.io/github/license/ibaciu6/rss-generator?style=flat-square"></a>
</div>

---

## Published feeds

All 49 feeds organized on the [index page](https://ibaciu6.github.io/rss-generator):

| Category | Count | Languages |
|----------|-------|-----------|
| Movies | 33 | 13 RO + 20 EN |
| TV Shows | 10 | EN |
| Episodes | 6 | RO |

[<kbd> 📥 Download OPML </kbd>](https://raw.githubusercontent.com/ibaciu6/rss-generator/main/feeds.opml) — import into Inoreader or any RSS reader, pre‑sorted into folders.

Feeds are enriched with TMDb posters and years. Sites failing 3 consecutive runs auto‑skip until recovery.

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

# Or serve locally (runs the full pipeline)
./scripts/serve.sh
```

---

## Features

- **49 streaming‑site feeds** sourced from FMHY — scraped via HTTP, cloudscraper, and Playwright
- **Poster & year enrichment** via TMDb API with rate‑limiting and in‑memory cache
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
       ├─ DedupStore (URL‑based dedup, 500 URLs/site cap)
       ├─ Feed writer (feedgen — RSS 2.0)
       └─ WordPress REST API (fallback)
```

- **`config/sites.yaml`** — master site list (XPath selectors, fetch method, category)
- **`core/engine.py`** — orchestration: shuffle, stagger, timeout (240s), concurrent (6 sites)
- **`scraper/fetcher.py`** — 3‑strategy fetch chain with anti‑detection and browser challenge retry
- **`scraper/parser.py`** — XPath 2.0 parsing via elementpath, falls back to lxml XPath 1.0
- **`core/feed.py`** — RSS 2.0 generation with WebSub hub, syndication tags, TMDb poster sizing
- **`core/tmdb.py`** — rate‑limited TMDb API client with in‑memory cache

---

## Adding or removing a site

Edit `config/sites.yaml`, then sync the derived files:

| File | Action |
|------|--------|
| `feeds.opml` | Add/remove `<outline>` element in the correct group |
| `index.html` | Add/remove `<tr>` row in the status table |
| `feeds/*.xml` | Delete file when removing; generated on next run when adding |

Run `PYTHONPATH=. python scripts/generate_index.py` to rebuild both `index.html` and `feeds.opml` automatically.

For new sites, use the interactive onboarding:

```bash
PYTHONPATH=. python scripts/onboard_site.py
```

---

## GitHub Actions

[`.github/workflows/update.yml`](.github/workflows/update.yml) — runs hourly + on push + manual dispatch:

1. Install Python + Playwright (cached)
2. Generate all feeds
3. Enrich with TMDb posters and years
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

> Architecture and development reference: [`PROJECT.md`](PROJECT.md) | [`CLAUDE.md`](CLAUDE.md)
