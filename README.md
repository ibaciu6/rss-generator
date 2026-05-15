## RSS Generator

**Generic, config‑driven RSS feed generator for streaming sites.** Generates 49 feeds with TMDb posters and year enrichment, published to GitHub Pages. Browse and subscribe at [ibaciu6.github.io/rss-generator](https://ibaciu6.github.io/rss-generator).

### Published feeds

All 49 feeds are available at `https://ibaciu6.github.io/rss-generator/feeds/*.xml`, organized in three sections on the index page:

- **Movies** — 33 movie feeds (13 RO, 20 EN)
- **TV Shows** — 10 EN show-level directory feeds
- **Episodes** — 6 RO episode-level feeds with season/episode numbering

[Download the OPML](https://ibaciu6.github.io/rss-generator/feeds.opml) to import all feeds into Inoreader or any RSS reader at once, pre-sorted into folders (Online-Movies-RO, Online-Movies-EN, Online-Episodes-RO, Online-TV-Series-EN).

Feeds are enriched with TMDb posters and years via a post-processing pipeline. Sites that fail 3 consecutive runs are auto-skipped until they recover.

### Features

- **49 streaming site feeds** sourced from FMHY, scraped via HTTP, cloudscraper, and Playwright
- **Poster & year enrichment** via TMDb API with rate limiting and caching
- **Index page** at `/` with live status, last update, and item counts
- **Downloadable OPML** for one-click import into Inoreader
- **Reader view** at `/reader.html` with Inoreader-style warm sepia theme
- **Auto‑skip**: sites failing 3 consecutive runs are silently skipped; counter resets on success
- **Config‑driven**: all site configs in `config/sites.yaml`
- **GitHub Actions automation**: scheduled rebuilds every hour + on push, deployed to GitHub Pages

### Quick start (local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# First time only – install Playwright browsers
playwright install chromium

# Generate feeds (from repo root)
PYTHONPATH=. python scripts/generate_feeds.py

# Enrich posters & fix feed formatting
PYTHONPATH=. python scripts/enrich_posters.py
PYTHONPATH=. python scripts/fix_feeds.py

# Regenerate index page + OPML
PYTHONPATH=. python scripts/generate_index.py

# Serve locally (runs enrich + fix first)
./scripts/serve.sh
```

### GitHub Actions

The workflow [`.github/workflows/update.yml`](.github/workflows/update.yml) runs **hourly** (UTC), on pushes to `main`, and on manual trigger (`workflow_dispatch`). It installs dependencies, generates feeds, enriches posters via TMDb, post-processes formatting, regenerates the index + OPML, commits changes, and deploys to GitHub Pages.

Requires `TMDB_API_KEY` set as a repository secret for poster enrichment.

Enable **Settings → Actions → General → Workflow permissions: Read and write** and set **Settings → Pages → Source** to **GitHub Actions**.

### Security

- Automated secret scanning runs on pushes, pull requests, manual dispatch, and a daily schedule via [`.github/workflows/secret-scan.yml`](.github/workflows/secret-scan.yml).
- Security reporting guidance lives in [`SECURITY.md`](SECURITY.md).

Detailed architecture and development notes live in `docs/`.
