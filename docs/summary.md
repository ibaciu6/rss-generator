## Project Summary

This repository implements a generic, config‑driven RSS & Atom feed generator for arbitrary websites. It supports multiple scraping strategies (HTTP, Cloudflare‑aware client, and Playwright headless browser), flexible HTML parsing with XPath selectors, deduplication, and automated feed generation via GitHub Actions, with output suitable for hosting on GitHub Pages.

Key components:

- `core.config`: configuration models and YAML loader for `config/sites.yaml`.
- `scraper.fetcher`: multi‑strategy fetcher with automatic fallback and retries.
- `scraper.parser`: HTML parser using `lxml` and XPath selectors to extract items.
- `core.dedup`: URL‑based deduplication backed by `data/cache.json`.
- `core.feed`: feed generation with `feedgen` producing RSS 2.0 and Atom feeds.
- `core.engine` and `core.cli`: orchestration engine and CLI (`rss-generator generate`).
- `.github/workflows/update.yml`: CI workflow that periodically regenerates feeds and commits changes.

Feeds are intended to be hosted through GitHub Pages from the `feeds/` directory, making them consumable by standard RSS readers.

