## Goal
- Fix missing posters, enlarge to 500px, add CI-visible logging, future-proof deps/UAs.
- Expand agent skills: knowledge graphs, better commits, structured research, Playwright automation.

## Constraints & Preferences
- Universal 500px poster, not per-feed
- Verbose per-feed CI logging for enrich step
- Deps unpinned (`>=`) — latest on every `pip install`
- UAs from `fake-useragent` + dynamic fallback
- Full root access via `~/.rootpw` — install anything
- Caveman ultra default output

## Progress
### Done
- Posters `<img>` insert when no img tag (was replace-only)
- Poster width 300→500px, max-height 450→750px (`fix_feeds.py:POSTER_STYLE`)
- Per-feed verbose logging: `[OK/--] filename.xml | items=N posters=N years=N skipped=N future=N`
- `process_feed()` returns `(changed, dict)` with stats
- Category-based feed enrichment: streaming / article / none modes
- `scripts/enrichers/` package with modular enrichment:
  - `streaming_enricher.py`: TMDb posters/years, IMDb/trailer links, EpGuides
  - `article_enricher.py`: Full article content extraction with ad removal
  - `ad_remover.py`: Ad/boilerplate stripping utilities
- `enhance_mode`, `detail_article_selector`, `ad_selectors` in SiteConfig
- Tests: `test_enrich_feeds.py` (31 tests), `test_engine_site_filter.py` (9 tests), `test_enrich_posters.py` (36 tests)
- CI actions bumped: `cache@v5`, `upload-artifact@v7`, `upload-pages-artifact@v5`, `deploy-pages@v5`, `configure-pages@v6`
- Removed `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`
- `requirements.txt`: `==` → `>=` (unpinned), added `fake-useragent>=2.0`
- UA strategy: `fake-useragent` primary, version-range fallback (Chrome/Firefox/Edge 100-205, Safari 15-29, 103 UAs)
- CI run #1341: generate (4m38s) + deploy (55s) — green
- Caveman installed, ultra default via `~/.config/caveman/config.json`
- `~/.config/opencode/AGENTS.md` with permissions note
- Installed: `feedparser`, `batcat`, `ripgrep`, `fd-find`, `black`, `ruff`, `mypy`, `pre-commit`, `pip-tools`, `cookiecutter`, `poetry`, `uv`, `subliminal`, `telethon`, `pandas`, `pyarrow`, `openpyxl`

#### Skills installed (18 new)
- **Understand-Anything** (8): understand, understand-chat, understand-dashboard, understand-diff, understand-domain, understand-explain, understand-knowledge, understand-onboard — knowledge graphs for codebases
- **contextual-commits** (2): contextual-commit, recall — better commit messages
- **e2e-agent-skills** (3): playwright-automation-expert, playwright-cucumber-expert, selenium-cucumber-expert — Playwright/Selenium automation, directly relevant to rss-generator
- **Deep-Research-skills** (5): research, research-add-fields, research-add-items, research-deep, research-report — structured deep research
- Total: 32 skills (30 global `~/.agents/skills/` + 2 project `.agents/skills/`)

#### Commit ae0e7003
- feat(enrich): add category-based enrichment with article extraction mode
- Added `scripts/enrich_feeds.py` orchestrator with category→mode routing
- Created `scripts/enrichers/` package (streaming, article, ad_remover modules)
- Added tests: `tests/test_enrich_feeds.py`, `tests/test_engine_site_filter.py`
- Updated README.md with enrichment modes documentation

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- **500px universal poster** in `fix_feeds.py:POSTER_STYLE`
- **Poster insert**: prepend `<img src="{poster_url}"><br>` before existing content
- **Logging**: compact per-feed metric line, not per-item
- **`streaming_enricher.process_feed` return**: `(bool, dict)` for stats visibility
- **Deps unpinned**: `==` → `>=` — every CI pulls latest
- **UA**: `fake-useragent` (bundled real-world data) + version-range fallback
- **Caveman ultra**: via `~/.config/caveman/config.json`, plugin sets `.caveman-active` flag
- **Skills**: npx skills for Playwright+Selenium+commits (project-level), manual symlinks for Understand-Anything+Deep-Research (global)

## Next Steps
- Update SUMMARY.md after each session
- Remove Dependabot config (unnecessary with unpinned deps) or keep for docs

## Critical Context
- `fix_feeds.py:POSTER_STYLE` = `'style="width:500px;..." width="500" loading="lazy"'`
- `streaming_enricher.py:TMDB_ID_RE` = `r"/(movie|tv)(?:/[^/]+)?/(\d{4,})(?:/|$|-)"`
- TMDB poster: `https://image.tmdb.org/t/p/w500/{path}`
- CI schedule: hourly at :19 UTC
- `requirements.txt` uses `>=`
- Root password at `~/.rootpw`
- `~/.config/caveman/config.json: {"defaultMode": "ultra"}`
- Feeds with 0 posters: ridomovies, voxfilmeonline, filmeonline123, xfilme, serialeonline, etc.
- 32 skills total (30 global, 2 project)
- Project skills dir: `/mnt/d/Download/tools/rss-generator/.agents/skills/`

## Relevant Files
- `scripts/enrich_feeds.py`, `scripts/enrichers/streaming_enricher.py`, `scripts/fix_feeds.py`, `tests/test_streaming_enricher.py`
- `.github/workflows/update.yml`
- `requirements.txt`, `scraper/fetcher.py`, `config/sites.yaml`
- `SUMMARY.md` (this file)
