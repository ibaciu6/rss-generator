# Dev Harness Notes

## 1. Where each stage reads input and writes output

All stages read/write **in place** to `feeds/` directory:
- **Stage 1 (generate_feeds.py)**: Reads `config/sites.yaml` → writes `feeds/*.xml`
- **Stage 2 (enrich_feeds.py)**: Reads `feeds/*.xml` → rewrites same files with enrichment
- **Stage 3 (fix_feeds.py)**: Reads `feeds/*.xml` → rewrites same files with post-processing
- **Stage 4 (generate_index.py)**: Reads `feeds/*.xml` → writes `index.html`, `feeds.opml`
- **Stage 5 (generate_reader.py)**: Reads `feeds/*.xml` → writes `reader.html`, `feeds/manifest.json`

`data/cache.json` (DedupStore) and `data/tmdb_cache.json` are persistent state.

## 2. Global/persistent state and isolation strategy

| State | File | Purpose | Isolation |
|-------|------|---------|-----------|
| DedupStore | `data/cache.json` | URL history per site (500 max) | Copy to run dir, use `--cache` CLI arg |
| TMDb cache | `data/tmdb_cache.json` | 3-tier cache (memory/disk, 30d hit/5d miss TTL) | Copy to run dir, set `TMDB_CACHE_DISABLE=1` for tests |
| Feeds | `feeds/` | Generated XML | **Use temp dir for all runs** |
| Index/OPML | `index.html`, `feeds.opml` | Derived artifacts | Generate in run dir |
| Reader artifacts | `reader.html`, `feeds/manifest.json` | Static reader for GitHub Pages | Generate in run dir |

**CLI flags for isolation:**
- `core.cli generate --feeds-dir <path> --cache <path>`
- `enrich_feeds.py` uses `FEEDS_DIR` constant (need env var or monkey-patch)
- `fix_feeds.py` uses `FEEDS_DIR` constant
- `generate_index.py` accepts `feeds_dir`, `output_file`, `output_opml` params
- `generate_reader.py` uses hardcoded paths (need to override)

## 3. Network-dependent vs deterministic stages

**Network-dependent:**
- Stage 1: `generate_feeds.py` - HTTP/cloudscraper/Playwright fetches
- Stage 2 (article mode): `enrich_feeds.py` → article fetches via httpx
- Stage 2 (streaming mode): TMDb API calls via httpx
- `pull_published_feeds.py` - downloads from GitHub Pages

**Deterministic given inputs:**
- Stage 2 (streaming mode with cache hits): TMDb lookups from cache
- Stage 3: `fix_feeds.py` - pure XML/HTML transformations
- Stage 4: `generate_index.py` - reads feed XML, builds HTML/OPML
- Stage 5: `generate_reader.py` - reads feeds, builds manifest

## 4. Every transformation from raw generate → final XML

### Stage 1: generate_feeds.py (`core/engine.py`)
1. Fetch via strategy chain: HTTP → Cloudscraper → Playwright (with validators)
2. Parse HTML (XPath 2.0 via elementpath, fallback lxml XPath 1.0) or RSS/Atom/WordPress
3. Multi-page pagination (`/page/N/`)
4. Within-run dedup by link
5. Truncate to `max_items`
6. **Detail enrichment** (if `detail_title_selector`/`detail_description_selector`): fetch detail pages with 1-4s delay
7. **Filter items** (before TMDb):
   - "coming soon" titles
   - `title_filter_patterns` regex (title AND link)
   - `blocked_categories` via `category_selector`
8. Drop items missing title/link
9. DedupStore records URLs (feed contains all items from this run)
10. `generate_rss()` via feedgen → `_decorate_rss_file()`:
    - Poster sizing via `_normalize_description_html()` (TMDB downscaling, fixed width/style)
    - WebSub hub link, syndication tags, TTL=60
    - Relative links resolved to absolute via `urljoin`

### Stage 2: enrich_feeds.py (`scripts/enrich_feeds.py`)
**Routing by category/enhance_mode:**
- `streaming` (movies, episodes, cinema, torrents, releases): TMDb posters/years, IMDb/trailer/EpGuides
- `article` (blogs, news, cyber, tech, education, economy, local): Full article body extraction
- `none`: pass-through

**Streaming enricher (`streaming_enricher.py`):**
1. EpGuides link (before TMDb) - cuts at SxxEyy, looks up in `allshows.txt`
2. Already-enriched short-circuit (has TMDB poster + IMDb link)
3. TMDb resolution: TMDb ID from link → IMDb ID → search by cleaned title
4. Title validation (`_title_matches`)
5. Year append: `f"{title} ({year})"`
6. Poster + links: replaces first `<img>`, inserts trailer/IMDb links

**Article enricher (`article_enricher.py`):**
1. Fetch article page (httpx with proxy)
2. `extract_main_content()` - 16 CSS selectors ranked by prose yield after ad removal
3. `remove_ads_and_boilerplate()` - DEFAULT_AD_SELECTORS + AGGRESSIVE_AD_SELECTORS + extra
4. `truncate_content()` - visible text cap at 50,000 chars
5. `remove_placeholder_svgs()`
6. Featured image (og:image → twitter:image → img.featured → first ≥100px in article)
7. Prepend featured image if not already in body
8. Replace or prepend to description, mirror to `<content:encoded>`
9. Cap final description at 50,000 chars
10. **No "Read more" trailer** (deliberate)

**Ad removal (`ad_remover.py`):**
- 96 DEFAULT_AD_SELECTORS (always)
- AGGRESSIVE_AD_SELECTORS (when aggressive=True, default for article mode)
- Per-site `ad_selectors` from config (replaces 8-entry fallback in core/config.py)
- Named removal modules (`removals:` in config): comments, akismet-notice, sponsor-block, emoji-images, related-posts, social-share, head-meta, blank-embeds, the-tags, etc.

### Stage 3: fix_feeds.py
Runs to fixed point (max 4 passes):
1. `fix_next_image_url` - unwrap Next.js `/_next/image?url=`
2. `fix_poster_style` - forces `<img>` to canonical poster style (width 300 for poster cats, auto for article)
3. `strip_label_fields` - removes `<strong>Label:</strong>` fields per feed
4. `fix_title_year` / `add_year_from_url` - normalize trailing year to `(YYYY)`, extract from URL
5. `fix_search_links` - appends year to trailer/IMDb links (idempotent)
6. `strip_configured_chrome` - re-applies site's `removals` modules to description (safety net)
7. `dedupe_search_links` - collapse duplicate Trailer/IMDb anchors
8. `fix_description_html` - chains above

**Cosmetic filters** (`config/cosmetic-filters.txt`): uBlock-style rules with budgets, domain anchoring, `up=N` escalation, `noimg` guard.

### Stage 4: generate_index.py
- Groups by category (ordered sections)
- Builds dashboard (enabled/available/unavailable counts)
- Creates `index.html` with tables per section
- Creates `feeds.opml` with outlines per section

### Stage 5: generate_reader.py
- Builds `feeds/manifest.json` (sidebar TOC: folders, feed names, item counts, guids)
- Generates `reader.html` (same page as local_reader.py with ADAPTER swapped)

## 5. Existing CLI flags to reuse

| Script | Flags |
|--------|-------|
| `core.cli generate` | `--config`, `--cache`, `--feeds-dir`, `--site` (repeatable, name or feed_file) |
| `enrich_feeds.py` | `--site` (repeatable) |
| `fix_feeds.py` | `--site` (repeatable) |
| `generate_index.py` | No CLI args (uses defaults, call as function) |
| `generate_reader.py` | No CLI args |
| `refresh_feed.py` | positional names, `--site`, `--skip get|enrich|process`, `--no-report` |
| `pull_published_feeds.py` | `--base-url`, `--feeds-dir`, `--timeout`, `--workers`, `--keep-extra` |
| `./start.sh` | `generate`, `enrich`, `fix`, `index`, `all`, `one <site>`, `logs` |
| `./scripts/start_reader.sh` | `start`, `stop`, `restart`, `status`, `logs`, `open`, `regen` |

## 6. Key invariants from agent-guide.md

1. **Enrichment idempotency**: Stage 2 & 3 are idempotent; Stage 1 is NOT (regenerate throws away enrichment)
2. **kind vs category routing**: Series-ness from `kind` field, not category (showrss: category=torrents, kind=series)
3. **Aggressive ad-selector constraint**: Never put content containers (`.wrapper`, `.article-body`) in aggressive set
4. **Reader gutter sign**: `feed_token()` = `mtime_ns-size` for read state sync
5. **Stale feed handling**: 90 days, None for no dates (streaming/cinema)
6. **Failure handling**: Transient failures keep previous feed; source-gone (404/410) deletes
7. **TMDB title cleaning**: Noise tokens bounded by `\b` on both sides; year-is-not-noise retry logic

## 7. Assumptions

- Harness will use subprocess calls to real scripts with isolated directories via `--feeds-dir` and `--cache`
- For enrich/fix stages that use hardcoded `FEEDS_DIR`, we'll monkey-patch `sys.path` or use env var
- Replay mode will hook at `fetcher.py` level (record/serve HTTP responses)
- TMDb cache: in replay mode, serve from recording; in live mode with `--no-tmdb`, mark streaming as SKIPPED
- Baseline comparison: sample N baseline sites per new site matching category/kind/method/language
- Run directory naming: `<UTC-timestamp>_<short-git-sha>[_label]`
- All artifacts under `runs/` (gitignored)