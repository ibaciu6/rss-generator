# RSS Generator — Complete System Guide

**Read this first if you are a new agent (human or AI) picking up this repository.**
It describes what the project does, how every stage works internally, and the
invariants that will silently break things if you violate them.

For *how to add a site*, see [`PROJECT.md`](../PROJECT.md) — that is the
site-authoring handbook. This document is the architecture/wiring reference.

---

## 1. What this project does

A config-driven feed generator. It scrapes ~73 streaming, torrent, cinema, blog
and news sites and turns each into a valid RSS 2.0 feed in `feeds/`, then enriches
those feeds with TMDb posters, release years, IMDb/trailer/EpGuides links and full
article bodies, post-processes the HTML, and publishes everything (feeds +
`index.html` + `feeds.opml`) to GitHub Pages.

The entire site list lives in **`config/sites.yaml`**. No site is hardcoded in
Python. Adding a feed means adding a YAML entry.

Current scale: **73 configured sites, 70 enabled** (3 disabled as verified
duplicates), published at `https://ibaciu6.github.io/rss-generator/`.

### Site distribution (useful for reasoning about the pipeline)

| Dimension | Breakdown |
|---|---|
| `method` | `rss` 42, `http` 21, `playwright` 10 |
| `category` | cyber 16, cinema 14, movies 9, blogs 9, episodes 6, news 5, tech 4, torrents 3, economy 2, local 2, education 2, releases 1 |
| `kind` | movie 23, series 7, undeclared 43 |
| `language` | `ro` 47, `en` 26 |

Note that **42 of 73 sites are `method: rss`** — native feeds that skip HTML
scraping and XPath entirely. Most of the "interesting" scraping complexity applies
to the remaining 31.

---

## 2. Commands

```bash
# Interactive control menu (reader controls + pipeline steps)
./start.sh

# Pipeline steps individually
./start.sh generate | enrich | fix | index | all
./start.sh logs                      # tail the troubleshooting log
PYTHONPATH=. python3 scripts/generate_feeds.py

# Single site. `scripts/generate_feeds.py` forwards argv to the same parser, so
# both of these are equivalent (and neither needs PYTHONPATH).
python3 -m core.cli generate --site showrss
python3 scripts/generate_feeds.py --site showrss.xml   # both name forms work

# ONE feed through the WHOLE flow: get -> enrich -> process. Use this when
# iterating on a single site; it is the same order CI runs.
./start.sh one gabriel-ursan
./start.sh one ghacks thehackernews        # several at once
python3 scripts/refresh_feed.py gabriel-ursan --skip get   # re-enrich only

# Local reader (Inoreader-style UI)
./scripts/start_reader.sh            # menu
./scripts/start_reader.sh start|stop|restart|status|logs|open|regen

# Static site preview
./scripts/serve.sh [port]            # default 8080; reader is a different server

# Tests / lint
PYTHONPATH=. python3 -m pytest tests/ -q
python3 -m ruff check .
```

`start.sh` sources the gitignored `.env` with `set -a`, so `TMDB_API_KEY` reaches
every pipeline step. An already-exported variable wins over the file.

---

## 3. Repository map

```
config/sites.yaml          SOURCE OF TRUTH — 73 site definitions
core/
  config.py                SiteConfig / Config dataclasses, load_config, validation
  engine.py                GenerationEngine — orchestration, fetch ladder, filtering
  feed.py                  RSS writing (feedgen), poster sizing, failure feeds
  dedup.py                 DedupStore — per-site URL history in data/cache.json
  tmdb.py                  TMDb client: lookup/search, 3-tier cache, rate limit
  logging_utils.py         structlog JSON configuration
  cli.py                   argparse entry: `generate`, `onboard-site`
  onboarding.py            Interactive site discovery (869 lines, separate concern)
scraper/
  fetcher.py               Fetcher — http -> cloudscraper -> playwright chain
  parser.py                Parser — XPath 2.0, RSS/Atom, WordPress JSON, dates
scripts/
  generate_feeds.py        wrapper: fixes sys.path, forwards argv -> core.cli
  enrich_feeds.py          ORCHESTRATOR: category -> enrichment mode routing
  enrichers/
    streaming_enricher.py  TMDb posters/years, IMDb/trailer links, EpGuides
    article_enricher.py    full article body extraction
    ad_remover.py          ad/boilerplate removal, content extraction
  fix_feeds.py             post-processing: years, poster style, link fixes
  refresh_feed.py          stages 1-3 for named feeds only, + body-length report
  generate_index.py        index.html + feeds.opml
  local_reader.py          local reader: stdlib HTTP server + embedded HTML/JS/CSS
  start_reader.sh          reader control menu
  restore_published_feeds.py  re-download last published feeds before generating
  refresh_wayback_mirrors.py  refresh RSS fallback mirrors
  audit_feeds.py, onboard_site.py, restore_*.py — maintenance tools
start.sh                   main control menu + session logging
data/
  allshows.txt             EpGuides title->slug mirror (13.7k lines, 7-day TTL)
  tmdb_cache.json          TMDb result cache (30d hit / 5d miss TTL)
  cache.json               DedupStore URL history
feeds/                     generated XML (gitignored)
index.html, feeds.opml     derived artifacts (gitignored)
tests/                     pytest suite, mirrors core/ and scraper/
```

`feeds/`, `index.html`, `feeds.opml`, `.env`, `logs/` are all **gitignored**.
Feeds are published as a Pages artifact, never committed.

---

## 4. The pipeline

```
config/sites.yaml
      │
      ▼
[1] generate_feeds.py ──► feeds/*.xml        (scrape + parse + write RSS)
      │
      ▼
[2] enrich_feeds.py ───► feeds/*.xml        (posters, years, links, article bodies)
      │
      ▼
[3] fix_feeds.py ──────► feeds/*.xml        (year format, poster style, link fixes)
      │
      ▼
[4] generate_index.py ─► index.html, feeds.opml
```

`start.sh all` and `scripts/start_reader.sh regen` run exactly this order, which
mirrors `.github/workflows/update.yml`.

**Each stage reads and rewrites the same XML files in place.** Stage 2 and 3 are
both idempotent by design (see §13).

**Stage 1 is not idempotent with respect to stage 2.** Generating rewrites the
feed from the site's own RSS, which throws away the previous run's article
bodies. Run a bare `./start.sh generate` and every blog feed silently reverts to
its bare RSS excerpt until stage 2 runs again. Anything that generates must
therefore also enrich — which is why `scripts/refresh_feed.py` exists:

| Command | Runs |
| --- | --- |
| `./start.sh one <site>…` | stages 1→2→3 for the named feeds only |
| `PYTHONPATH=. python3 scripts/enrich_feeds.py --site <site>` | stage 2 only |
| `PYTHONPATH=. python3 scripts/fix_feeds.py --site <site>` | stage 3 only |

All three accept a site `name` or a `feed_file` (with or without `.xml`),
resolved by `core.config.resolve_feed_files` — the same matching rule the
generator uses, so the input never differs between stages. An unknown name is a
**hard error**, not an empty run: a typo that silently swept all 70 feeds would be
indistinguishable from success.

`refresh_feed.py` finishes by printing the body-text length of every item it
touched. A blog/news item under ~800 characters is showing the site's own RSS
excerpt rather than the fetched article, so the number makes "the article is
truncated again" visible without opening the XML.

`index.html` / `feeds.opml` are deliberately **not** rebuilt by
`refresh_feed.py`: they are global derived artifacts covering every feed, and the
local reader reads `feeds/` directly. Use `./start.sh index` when the site list
itself changed.

In CI, one extra step runs *before* stage 1:

```
restore_published_feeds.py  ──► feeds/*.xml  (re-download last published copies)
```

This exists because feeds are gitignored, so a scheduled runner starts with an
empty `feeds/`. Without it, `GenerationEngine`'s last-known-good protection has
nothing to fall back to and a transient scrape failure would blank a feed.

---

## 5. Stage 1 — Generation

### 5.1 Config model (`core/config.py`)

`SiteConfig` is a **frozen dataclass**; `Config` holds `sites: list[SiteConfig]`.

`load_config()` is strict:

- **Unknown keys raise** `ValueError`. A typo in `sites.yaml` fails loudly rather
  than being silently ignored.
- `feed_file` must be a bare `*.xml` filename (no directories) and must be unique
  across sites.
- `url` must start with `http://` or `https://`.
- `item_selector` / `title_selector` / `link_selector` must be non-empty **unless**
  `method: rss`.
- `kind` ∈ {`None`, `movie`, `series`}; `enhance_mode` ∈ {`None`, `streaming`,
  `article`, `none`}; `title_transform` ∈ {`None`, `title_case`}.
- `language` must match `^[a-z]{2}(-[A-Z]{2})?$`.
- `method: httpx` is normalised to `http` on load.

`required_content_marker_groups` is parsed by `_parse_marker_groups()` into an
**OR-of-ANDs** structure: `[["a","b"], ["c"]]` means the page passes if it contains
(`a` AND `b`) **or** `c`. A flat `required_content_markers` list is treated as a
single AND group.

### 5.2 Orchestration (`core/engine.py`)

`GenerationEngine.run()`:

1. `_select_sites()` filters out `enabled: false` sites, then applies the optional
   `only_sites` filter (matches site `name` **or** `feed_file` stem, with or
   without `.xml`; returns unmatched requests for a warning log).
2. Raises `ValueError` if nothing is selected.
3. Loads `DedupStore`, creates one `Fetcher` for the whole run.
4. **Shuffles** the site list, then starts each in its own anyio task with
   `stagger_delay = idx * random.uniform(0.5, 1.5)` so requests don't burst.
5. Each site runs under an `anyio.Semaphore(MAX_CONCURRENT_SITES)` (= 6) and an
   `anyio.move_on_after(SITE_TIMEOUT_SECONDS)` (= 240 s) cancel scope.
6. `finally:` closes the fetcher and **saves the dedup store** even on failure.

Shuffling matters: it spreads load so the same sites aren't always hit first.

### 5.3 The extraction ladder (`_extract_items`)

Order of attempts — each step only runs if the previous raised:

1. **`method: rss`** → fetch the URL, `parse_rss_items()`. Tries `site.url` then
   each `fallback_urls`.
2. **HTML scrape** (`_extract_html_items`) — for every method in
   `_candidate_fetch_methods(site)` (the site's method first, then the remaining
   of `http → cloudscraper → playwright`), and for `require_markers` ∈ {True, False}
   when the site declares marker groups.
3. **Native RSS fallback** (`_candidate_rss_urls`) — `<listing>/feed/` for the
   listing URL and each fallback, then `<root>/feed/`. Many WordPress themes
   publish a per-archive feed that is populated when the root one is empty.
4. **WordPress REST fallback** (`_candidate_wordpress_urls`) —
   `<root>/wp-json/wp/v2/posts?per_page=<max_items>&_embed=1`. `_embed=1` is what
   makes `wp:featuredmedia` available for the featured image.

All errors are accumulated and joined into one `RuntimeError` so a failure report
names every strategy that was tried.

`pages > 1` fetches `/page/2/` … `/page/N/` **after** the first page succeeds and
appends items, building a deeper pool *before* filtering. Pagination failures are
non-fatal.

### 5.4 Fetch strategy chain (`scraper/fetcher.py`)

`Fetcher.fetch(url, method, validator, ...)` walks a strategy chain. The chain
order depends on the requested method:

| Requested | Chain |
|---|---|
| `http` / `httpx` | http → cloudscraper → playwright |
| `cloudscraper` | cloudscraper → http → playwright |
| `playwright` | playwright → http → cloudscraper |

If Playwright isn't importable it is stripped from the chain.

**The `validator` callback is what drives escalation.** If a strategy returns 200
but the content fails validation (Cloudflare interstitial, missing listing
markers), the validator raises and the *next* strategy runs. This is the main
reason bots get past soft blocks.

- `_fetch_http` — `httpx.AsyncClient`, tenacity retry ×3, random 0.5–2 s pre-delay.
- `_fetch_cloudscraper` — synchronous, run via `anyio.to_thread`.
- `_fetch_playwright` — headless Chromium with `--disable-blink-features=AutomationControlled`,
  an init script that masks `navigator.webdriver`, random desktop viewport, locale
  `en-US`. It polls up to 4× while `_looks_like_browser_challenge(content)`.
  Honours `playwright_wait_selector` and `playwright_scroll_to`.

**User agents are desktop-only by design.** `_build_user_agents()` filters out any
UA containing `Android|iPhone|iPad|iPod|Mobile|Windows Phone|MQQBrowser|CriOS|FxiOS`.
Mobile UAs trigger device-based redirects (e.g. Cinemagia → `m.cinemagia.ro`) that
serve a different DOM and yield zero parseable items.

`anyio.CapacityLimiter(2)` caps concurrent Playwright sessions (two Chromiums ≈
600 MB; GitHub runners have 2 vCPU / 7 GB). It's passed with `cancellable=True`
so a site's 240 s timeout frees the slot immediately even though the worker thread
keeps running.

Proxy: `RSS_GENERATOR_PROXY_URL` env var, if set.

### 5.5 Response validation (`_validate_fetch_result`)

Checked on **every** fetch (including detail pages, with
`require_listing_markers=False`):

1. `blocked_final_hosts` / `allowed_final_hosts` — host allow/deny after redirects.
2. **Listing markers** (when required and configured) — at least one marker *group*
   must match in full.
3. **Blocked content markers** — a global list (`GENERIC_BLOCKED_CONTENT_MARKERS`:
   "just a moment...", "cf-challenge", "error code: 522", …) plus per-site
   `blocked_content_markers`. Checked *always*, even when markers are relaxed.

Marker relaxation only relaxes requirement (2), and logs
`site.listing_relaxed_markers` when it succeeds that way.

### 5.6 Parsing (`scraper/parser.py`)

- **XPath 2.0** via `elementpath`, falling back to lxml XPath 1.0. lxml's
  `HtmlElement` subtypes can't be traversed by elementpath, so HTML nodes are
  re-parsed as plain XML first.
- **Selector alternatives**: any selector may be `"a || b || c"`. The first
  candidate that returns nodes wins — a cheap way to encode site fallbacks.
- Titles accept `title_transform: title_case` for ALL-CAPS sites.
- `allow_empty_title: true` keeps items with no title (used by some listings).
- Dates: `datetime.fromisoformat` → a list of `strftime` formats → `dateutil` with
  `fuzzy=False` → `None`.
- `parse_rss_items` prefers `content:encoded` over `description`, falls back to
  `guid` for the link, and dispatches Atom (`<feed>`/`<entry>`) separately.
  Reddit's `submitted by … [link] [comments]` boilerplate is stripped.
- `parse_wordpress_posts` builds a description from the featured image
  (`_embedded["wp:featuredmedia"][0].source_url`) plus excerpt or content.

### 5.7 Per-item processing (`_process_site`)

Strictly ordered:

1. extract items
2. `_deduplicate_items` — **within-run**, by link
3. truncate to `max_items`
4. `_enrich_items` — only if `detail_title_selector` or
   `detail_description_selector` is set; fetches each detail page with a random
   1–4 s delay between fetches
5. `_filter_items` — quality filters (below)
6. drop items missing title or link; error if nothing is left
7. `dedup.filter_new(site, links)` — **records** seen URLs (the return value is
   deliberately discarded: the feed contains all items from this run)
8. `generate_rss(...)`

`_filter_items` drops: titles containing "coming soon", titles **or links**
matching any `title_filter_patterns` regex, and items whose `category_selector`
values intersect `blocked_categories`. This runs *before* enrichment so junk never
reaches TMDb.

### 5.8 Dedup store (`core/dedup.py`)

`data/cache.json`, one `OrderedDict` of URLs per site, capped at
`DEFAULT_MAX_URLS_PER_SITE = 500` with oldest-first eviction. The cap exists
because feeds only carry ~24 items; once a URL has rolled out it never returns.

**It is a history recorder, not a feed filter.** `filter_new` returns new URLs but
the engine discards them — the published feed always contains every item from the
current run, so items don't vanish from a reader between runs.

### 5.9 Feed writing (`core/feed.py`)

`generate_rss()` uses `feedgen`, then `_decorate_rss_file()` re-opens the XML with
`ElementTree` to upsert `<link>`, `<pubDate>`, `<ttl>` (60), `sy:updatePeriod`
(`hourly`), `sy:updateFrequency` (1), and a WebSub `<atom:link rel="hub">` pointing
at `pubsubhubbub.appspot.com`.

Item links are resolved against `site_url` via `urljoin`.

**Poster sizing is centralised here.** `_normalize_description_html()` rewrites
`image.tmdb.org/t/p/{wNNN|original}` to a size chosen by `_tmdb_size_for()`, sets
an explicit `width` attribute, and replaces the `style`. `POSTER_IMG_WIDTH = 300`
(150 for `category: cinema`) and `poster_style_for()` are the single source of
truth — **`scripts/fix_feeds.py` imports these same constants**, so change them in
one place only.

`RSS_FEED_PUBLIC_BASE` env var makes the `<link rel="self">` absolute in CI.

**Failure feeds.** On any site error, `_write_failure_feed()` writes a valid RSS
feed titled `"<name> (unavailable)"` explaining the failure — *but only if there is
no healthy existing feed to keep.* If the current file is a real feed, it is left
alone (`site.keeping_old_feed`) so subscribers keep seeing real items through a
transient outage. Playwright "Call log:" noise is stripped and the reason is capped
at 800 chars. `_remove_legacy_sidecar_outputs()` deletes any stale `.atom.xml`.

---

## 6. Stage 2 — Enrichment

`scripts/enrich_feeds.py` is the orchestrator. It loads `sites.yaml`, builds a
`feed_file → {category, url, base_url, enhance_mode, detail_article_selector,
ad_selectors}` map, then walks `sorted(FEEDS_DIR.glob("*.xml"))` dispatching each
feed to a mode.

### 6.1 Mode routing

`_resolve_mode(category, override)`: a per-site `enhance_mode` wins; otherwise the
category default comes from `CATEGORY_ENRICHMENT`.

| Mode | Categories |
|---|---|
| `streaming` | `movies`, `episodes`, `cinema`, `torrents`, `releases` |
| `article` | `blogs`, `news`, `cyber`, `tech`, `education`, `economy`, `local` |
| `none` | (only via explicit `enhance_mode: none`) |

**An unknown category falls back to `streaming`, not `none`.** A newly added site
keeps the enrichment it had before per-category routing existed rather than
silently losing it. When you add a category to `sites.yaml`, add it here too.

### 6.2 `TMDB_API_KEY` handling

If unset, `streaming` mode is skipped entirely (`changed=False`) and a warning is
printed — falling through would burn one failing API call per item. `article` mode
doesn't need TMDb and **still runs**.

### 6.3 Series-ness comes from `kind`, not `category`

```python
kind = feed_kinds.get(path.name)
process_streaming_feed(path, is_series_feed=None if kind is None else kind == "series")
```

- `kind: series` → always treat as TV.
- `kind: movie` → always treat as movie.
- undeclared (`None`) → let `process_feed` fall back to its per-item heuristic
  (`SxxEyy` / `NxM` marker in the title).

This matters because **`showrss` is `category: torrents` but `kind: series`**. A
category-based check would have broken its EpGuides links.

### 6.4 Streaming mode (`enrichers/streaming_enricher.py`)

Per item, in order:

1. **EpGuides link** — resolved *before* the TMDb lookup so series TMDb has no
   poster for still get linked. For a TV item, `_epguides_series_title()` takes
   everything before the `SxxEyy` marker, then `_find_epguides_slug()` looks it up
   in the `allshows.txt` map (tolerating a leading "The"). Misses are recorded in
   `epguides_misses` for a second pass.
2. **Already-enriched short-circuit** — if the description contains both
   `image.tmdb.org` and `www.imdb.com/find?`, skip the TMDb round-trip entirely.
3. **TMDb resolution** — `_lookup_link()` tries a TMDb id parsed from the item link
   (`/movie/…/123`, `/tv/…/456`), then an IMDb id (`tt\d{7,8}`) via
   `/find`. Failing both, it searches by cleaned title.
4. **Title validation** — `_title_matches()` rejects the result if the TMDb title
   doesn't plausibly match the feed title (prevents wrong-id posters).
5. **Year append** — `f"{title} ({year})"` if the title has no year yet.
6. **Poster + links** — replaces the first `<img>` in both `<description>` and
   `<content:encoded>`, then inserts trailer + IMDb search links right after it.
   Creates a `<description>` when absent (native RSS/Atom feeds like Reddit).

`_clean_search_title()` is the torrent-noise stripper — quality tags, codecs,
scene groups, dot-separated names. Without it TMDb returns nothing for release
titles.

`resolve_epguides_misses()` is the **second pass**: re-downloads `allshows.txt`
once, then retries every unresolved item. If the series still isn't in the list it
attaches an EpGuides **site-search** link (a Google CSE URL) so every TV item ends
up linked. Items are re-keyed by `guid`/`link` because the tree was re-parsed.

### 6.5 TMDb client (`core/tmdb.py`)

`MovieInfo` has exactly four fields: `poster_url`, `year`, `title`, `release_date`.

Three cache tiers:
1. `id_cache` / `search_cache` — in-memory, per process.
2. `data/tmdb_cache.json` — write-through, flushed via `atexit` + atomic
   `os.replace`. **Hits live 30 days, misses only 5 days** so titles added to TMDb
   later get another chance.
3. `TMDB_CACHE_DISABLE=1` bypasses all of it (used by tests).

Search cache keys fold the title to `[a-z0-9]` only. `_rate_limit()` enforces
`MIN_GAP_SECONDS = 0.25` (TMDb allows 4 req/s). Every failure returns an empty
`MovieInfo()` rather than raising.

### 6.6 Article mode (`enrichers/article_enricher.py`)

Per item: fetch the page → `extract_main_content()` → `remove_ads_and_boilerplate()`
→ `truncate_content()` → `remove_placeholder_svgs()` → optionally prepend the
featured image → replace (or prepend to) the description, mirroring into
`<content:encoded>`.

There is deliberately **no "Read more at source" trailer**. The full body is
already inline, so the link is misleading, and every RSS reader already links
the item title to `<link>`. It used to be appended to all 19 article feeds.

There is also **no source link in the reader panel** (`openPanel()` in
`local_reader.py`): the panel is for reading, not for navigating away, so the
article body is the end of the line.

The featured image is prepended to the body, but **only if the body does not
already show the same photo**. WordPress derives thumbnails as `-{w}x{h}` before
the extension, so a featured image is often a smaller copy of a photo the
article contains — without the check, hoinaru rendered its lead photo twice.
`body_contains_image()` compares URLs with the size suffix stripped.

`ArticleEnrichConfig.aggressive_mode` **defaults to `True`**, so the aggressive
selector set applies to every article feed. See the invariant below.

Descriptions are capped at `MAX_DESCRIPTION_LENGTH = 50_000`.

An extraction whose visible text is under `MIN_BODY_TEXT = 200` is treated as a
fetch failure: the item keeps the feed's own description and counts as `skipped`.
See invariant 14.

### 6.7 Ad removal (`enrichers/ad_remover.py`)

- `DEFAULT_AD_SELECTORS` (96 entries) — always applied.
- `AGGRESSIVE_AD_SELECTORS` — applied only when `aggressive=True`; sidebars, share
  bars, related-post blocks, comment forms, cookie banners, a11y helpers.
- `remove_ads_and_boilerplate(html, extra_selectors, aggressive)` composes
  `(_get_stronger_ad_selectors() if aggressive else DEFAULT_AD_SELECTORS) + extra_selectors`.
  Every selector is tried inside a `try/except` so one bad CSS selector can't abort
  the run.

`extract_main_content()` tries 16 CSS selectors in order (`article`,
`.entry-content`, `[itemprop='articleBody']`, …), strips `DEFAULT_AD_SELECTORS`
from the winner, and truncates at footer boundaries. Falls back to cleaning the
whole page.

`extract_featured_image()` prefers `og:image`, then `twitter:image`, then
`img.featured`, then the first ≥100px image inside the article.

**Per-site removals live in `config/sites.yaml`, not in the global sets.** A site's
`ad_selectors` arrives as `extra_selectors`. This is the only correct place for a
theme-specific block: the global sets are shared by all 40 article feeds, so a
selector that is an ad on one site may be content on another.

> #### Case: gabriel-ursan wraps its promos inside `<article>`
>
> That theme's `<article>` element contains four promotional `<aside>` siblings of
> the real body — `promo-articol` (a Kraken/XTB referral ad labelled
> *Publicitate*), `abonare-articol` (newsletter), `card-autor` (author bio) and
> `promo` (`id="promo-curs"`). `extract_main_content()` matches `article`, so all
> four ride along and land in the feed.
>
> The fix is four **class** selectors in that site's `ad_selectors`:
> `aside.promo-articol`, `aside.abonare-articol`, `aside.card-autor`, `aside.promo`.
> They are class-scoped rather than a bare `aside` on purpose: a bare tag selector
> would delete article content the moment the theme changes, and that is the same
> failure mode as the aggressive-set invariant below. Note that declaring
> `ad_selectors` in `sites.yaml` **replaces** the 8-entry fallback in
> `core/config.py`, so the site list has to keep those too — pinned by
> `test_site_selectors_keep_the_config_defaults`.
>
> `tests/test_ad_remover.py::TestGabrielUrsanPromos` reproduces the theme shape and
> asserts the promos go and the body stays.

> #### Case: hoinaru needs a detail selector, not more ad selectors
>
> hoinaru.ro is a WordPress/WPBakery theme whose page is dominated by a `<style>`
> block. `extract_main_content()` matches none of its default selectors, so it
> falls back to cleaning the **whole page** — and the result lands just over
> `MAX_DESCRIPTION_LENGTH`. The `len(cleaned) <= MAX_DESCRIPTION_LENGTH` check then
> rejected every item, so the feed shipped 10/10 excerpts.
>
> The fix is `detail_article_selector: div.w-post-elm.post_content`, plus
> `div.crp_related` in `ad_selectors` for the related-posts block that sits inside
> the content div. `tests/test_ad_remover.py::TestHoinaruExtraction` reproduces the
> theme shape.

> #### Case: manafu's `.content` wrapper repeats the title
>
> manafu.ro's `.content` element wraps an `<h2>` title, a byline/date/comment
> list, share buttons and a tags footer around the real body. Extraction
> matched `.content`, so the reader showed the headline twice and every article
> carried "Articole similare", "Comenează" and "Tags:". The fix is
> `detail_article_selector: .entry`, plus `div.crp_related` for the related-posts
> block inside it. `tests/test_ad_remover.py::TestManafuExtraction` reproduces
> the theme shape.

> #### Invariant: `truncate_content()` must enforce its cap
>
> It used to trim only the single text node that crossed `max_chars` and then
> return, leaving the rest of the document in place. A page dominated by one huge
> text node — a `<style>` block — therefore sailed straight past the cap, which is
> what made hoinaru's extraction exceed `MAX_DESCRIPTION_LENGTH` and skip. It now
> drops every text node after the cutoff. `TestTruncateContent` pins it, including
> the single-huge-node case.

> #### Invariant: the aggressive set must never contain a content container
>
> `remove_ads_and_boilerplate()` runs on the **already-extracted** article body, not
> the full page. A selector like `.wrapper`, `.container` or `.article-body` in the
> aggressive set therefore deletes exactly the text `extract_main_content()` just
> selected. This is not theoretical — a container nested inside the extracted body
> (common on WordPress themes) loses its whole paragraph.
>
> `tests/test_ad_remover.py` pins this: `test_aggressive_set_contains_no_content_containers`
> and `test_aggressive_preserves_nested_containers` both fail if a container
> selector is reintroduced. Keep that list narrow for the same reason
> `aggressive_mode` defaults to `True`.

---

## 7. Stage 3 — Post-processing (`scripts/fix_feeds.py`)

Deterministic cleanups, keyed by feed name where the fix is site-specific
(`FIXES` dict):

- `fix_next_image_url` — unwraps Next.js `/_next/image?url=…`.
- `fix_poster_style` — forces every `<img>` to the canonical poster style using
  `poster_width_for_category()`, and downscales the src. Imports the constants from
  `core.feed`.
- `strip_label_fields` — per-feed removal of leftover `<strong>Label:</strong>`
  fields (a safety net for feeds generated before the selectors were fixed).
- `fix_title_year` / `add_year_from_url` — normalise a trailing `2026` into
  `(2026)`, and pull a year out of the URL when the title has none.
- `fix_search_links` — appends ` (year)` to trailer/IMDb search links, and is
  itself idempotent (skips when the year is already present, bare or
  percent-encoded).

---

## 8. Stage 4 — Index & OPML (`scripts/generate_index.py`)

Builds `index.html` (grouped by category, with a dashboard of counts) and
`feeds.opml` for import into a reader.

**Both exclude `enabled: false` sites**, and the dashboard renders
`70 (+3 disabled)`. `_get_feed_info()` reads each feed's metadata (title, item
count, latest date) from the generated XML.

Because it's a derived artifact, **regenerate it whenever `config/sites.yaml`
changes** — the project convention is to commit `index.html`/`feeds.opml`
alongside site changes even though they are gitignored locally.

---

## 9. Local reader (`scripts/local_reader.py`)

A single-file app: a stdlib `http.server` handler serving one embedded HTML page
plus a `/api` JSON endpoint that returns parsed feeds.

- `parse_feed(path, *, with_items=True)` — items, tags, plain text, date.
  `with_items=False` returns `items: []` plus an `item_count`, for callers that only
  need the count.
- **Removal modules** (`scripts/enrichers/removal_modules.py`) — reusable chrome
  removers referenced by name in a site's `removals:` list. Each module removes
  one category of chrome and is shared across every feed that lists it:

  | module | removes | evidence |
  |---|---|---|
  | `comments` | comment section + form, "Comenează" links | 13 sites, 65+20 markers |
  | `akismet-notice` | `p.akismet_comment_form_privacy_notice` | 3 sites, 16 hits |
  | `sponsor-block` | text-matched partner/sponsor blocks | 1 site, 8+21 hits |
  | `emoji-images` | `img[src*="fbcdn.net"]` (Facebook emoji) | 1 site, 45 images |
  | `related-posts` | "Articole similare" / `div.crp_related` | 2 sites |
  | `social-share` | share buttons | 2 sites |
  | `head-meta` | `<meta>` and `<noscript>` in the body | many sites, 31+30 hits |
  | `the-tags` | WordPress tags footer | 1 site |

  Modules run after `remove_ads_and_boilerplate()` and before the description is
  built. A site composes them in `config/sites.yaml`:

  ```yaml
  removals:
    - comments
    - akismet-notice
    - sponsor-block
  ```

  Unknown module names raise at config load. `ad_selectors` still works and is
  applied before modules.

  Three implementation constraints the scan forced:

  - **`NavigableString` has no `.parent`**, so `find_parent` raises on text
    matches. `_block_parent` walks `.parents` instead.
  - **Collect-then-remove.** Decomposing a host detaches sibling matches that
    `find_all` already returned; removing while iterating raises or skips.
  - **Romanian Unicode.** Sites use cedilla forms (U+015F `ş`) while regexes
    often use comma-below (U+2199 `ș`) — identical-looking, different code
    points. Match both.

  Module catalogue (from the 40-site / 555-article scan):

  | module | removes | shared by |
  |---|---|---|
  | `comments` | comment section + form, "Comenează" links | 13 sites |
  | `akismet-notice` | `p.akismet_comment_form_privacy_notice` | 3 sites |
  | `sponsor-block` | text-matched partner/sponsor blocks | 1 site |
  | `emoji-images` | `img[src*="fbcdn.net"]` | 1 site |
  | `related-posts` | "Articole similare" / `div.crp_related` | 2 sites |
  | `social-share` | share buttons | 2 sites |
  | `head-meta` | `<meta>`/`<script>`/`<style>`, plus `<noscript>` — see below | 30 sites |
  | `the-tags` | WordPress tags footer | 1 site |
  | `post-navigation` | `nav.post-navigation` (prev/next) | 1 site |
  | `promo-footer` | daily-offer / partner banner | 1 site |
  | `author-box` | author bio + "Articole: N" footer | 1 site |
  | `theme-icons` | any `<img>` under `/wp-content/themes/` | 5 sites |
  | `dedupe-images` | same photo twice inside the body | 12 sites |
  | `page-shell` | doctype + `<html>`/`<head>`/`<link>` from the fallback | 3 sites |
  | `gnews-banner` | "Add us as a source in Google News" CTA | 1 site |
  | `svg-sprites` | inline `<svg>` referencing theme sprite paths | 1 site |

  Two rules learned the hard way, both about *where* something is removed:

  - **The featured image is prepended after the modules run**, so a module
    cannot catch a theme asset or emoji that `extract_featured_image` picked as
    `og:image`. `ad_remover._NOT_FEATURED_IMG_RE` filters those too — otherwise
    schneier's `rss.png` came back on every article.
  - **`page-shell` must unwrap `<html>`/`<body>`, never decompose them.**
    Decomposing `<body>` deleted the whole article and every item looked empty.
    Only `head`/`link`/`title`/`base`/`noscript` are dropped outright.
  - **`<noscript>` must be unwrapped, not dropped, when it holds elements.**
    It plays two roles: a text-only one is a bot interstitial ("Enable
    JavaScript and cookies to continue") and should be dropped, but a
    lazy-loading theme wraps the article's *only* photo in
    `<noscript><img src=...></noscript>` (darknet-the-darkside), and deleting
    that takes the image with it. `head-meta` therefore decomposes
    `meta`/`script`/`style` outright and only decomposes a `<noscript>` that
    has no element children.
- `build_toc()` — the feed tree, grouped by `FOLDER_BY_CAT_LANG` (category ×
  language) with `FOLDER_FALLBACK = "Other"`. Each entry carries a `token`
  (`feed_token()`) so the client can detect a regenerated feed.
- `FOLDER_FALLBACK` catches unconfigured category/language combinations.
- **The TOC path is deliberately cheap.** It runs on every page load and every
  refresh, so it must not pay for per-item work it never uses: `build_toc()` calls
  `parse_feed(..., with_items=False)` and `_load_site_names()` is cached on the
  config's mtime/size. Parsing all 1208 items' HTML (one body is 471KB) plus
  re-reading `sites.yaml` per feed cost **6.5s** per load; the cheap path is ~0.8s.
  Do not "simplify" this back into a full `parse_feed()` loop.
- Client state in `localStorage`: read/unread sets, and the two column widths.
- **Read state is reset per feed, automatically.** `feed_token()` is
  `f"{mtime_ns:x}-{size:x}"` of the feed file; `api?list` publishes it per feed, and
  `syncReadState()` clears the read marks of every feed whose token changed since the
  last look. Regenerating a feed therefore makes its items unread again without any
  manual step. The first run of this logic seeds the tokens *without* wiping existing
  marks, so upgrading does not silently clear everyone's state. `syncReadState()` also
  drops read keys for feeds that no longer exist, so `localStorage` cannot grow
  unbounded. A manual **Reset read** button sits left of **Refresh feeds**.
- **Three-pane layout**: sidebar (feed list) │ news list │ preview panel, with
  two drag gutters.
- CSS custom properties `--sidebar-w` / `--panel-w` / `--head-h` drive layout.
  `--head-h: 48px` is shared by `.brand` and `.toolbar` so the header rows align.
- Gutter geometry: **the two columns are anchored on opposite edges, so the drag
  sign differs per gutter.** `#sidebar-gutter` sits on the sidebar's *right* edge
  and the sidebar is left-pinned, so dragging right widens it (`dir: +1`).
  `#panel-gutter` sits on the panel's *left* edge and `#panel` is `flex: 0 0`, so
  its right edge is pinned and dragging right *narrows* it (`dir: -1`). The math
  is `startPx + cfg.dir * (clientX - startX)`; a single shared sign makes one
  handle slide away from the cursor. The keyboard arrows scale by `cfg.dir` too,
  so they move the *handle* rather than the raw width.
- Drag measures the **rendered** width on start (self-healing against stored-value
  drift) and stored values are re-clamped against the current window on load.
- `selectFeed()` / `markRead()` address rows by `data-file` with `CSS.escape`, not
  by interpolated CSS selectors.

`scripts/start_reader.sh` is the control script (`start|stop|restart|status|logs|open|regen`).
It gates colour output on `[ -t 1 ]` and `NO_COLOR` so captured output is clean.

---

## 10. Ops scripts

**`start.sh`** — main menu. Sources `.env`, offers pipeline steps and reader
controls, and writes a session log to `logs/start.log` (gitignored): timestamped
header with host/git/interpreter, whether `TMDB_API_KEY` is set (**length only,
never the value**), each step with its exit code, and command output. Rotates to
`start.log.1` past 1 MiB. ANSI colour is stripped from the log but kept on the
console.

**`scripts/serve.sh`** — `python3 -m http.server` over the repo root for previewing
`index.html`. This is *not* the reader; use `start_reader.sh` for that.

**`scripts/restore_published_feeds.py`** — re-downloads the published feeds from
GitHub Pages into `feeds/`, keeping only healthy ones. Runs first in CI.

**`scripts/refresh_wayback_mirrors.py`** — refreshes Wayback Machine RSS fallbacks
for sites that have gone down.

**`scripts/audit_feeds.py`** — health report across all generated feeds.

---

## 11. CI/CD

| Workflow | Trigger | Does |
|---|---|---|
| `update.yml` | push to `main`, hourly at `:19` UTC, manual | Full pipeline → GitHub Pages |
| `lint.yml` | push/PR | `ruff check .` |
| `secret-scan.yml` | push/PR | Gitleaks |
| `auto-merge.yml` | Dependabot PRs | auto-approve + merge |

`update.yml` internals worth knowing:

- `RSS_FEED_PUBLIC_BASE` and `TMDB_API_KEY` from secrets; optional
  `RSS_GENERATOR_PROXY_URL`.
- Caches the Playwright browser and `data/tmdb_cache.json` across runs.
- **Runs the test suite before generating.**
- `concurrency: {group: pages, cancel-in-progress: true}` — a new run cancels the
  old one.
- The generate step is `continue-on-error: true`; enrich and fix are gated on
  `steps.generate_feeds.outcome == 'success'`. **A generation crash therefore
  skips enrichment** and still deploys the last good feed files.
- 0-byte feeds are deleted; `site.error` lines are extracted from the JSON log into
  `logs/failed_feeds.txt` and uploaded as an artifact.
- Index/OPML/feeds are staged into `.site/` and deployed; then the WebSub hub is
  pinged for every enabled feed so readers refetch immediately.

---

## 12. Tests

139 tests, all offline (no live site or TMDb dependency). `tests/` mirrors the
source layout: `test_config`, `test_engine`, `test_engine_site_filter`,
`test_fetcher`, `test_parser`, `test_feed`, `test_dedup`, `test_tmdb_cache`,
`test_fix_feeds`, `test_index`, `test_onboarding`, `test_refresh_wayback`,
`test_enrich_feeds`, `test_ad_remover`.

```bash
PYTHONPATH=. python3 -m pytest tests/ -q
```

Enrichment tests stub the network. When changing enrichment behaviour, assert on
**idempotency** (running twice must not grow counts) — that is the property CI
depends on, since `update.yml` runs hourly and would silently accumulate
duplicate links otherwise.

---

## 13. Invariants and gotchas

These are the things that will silently corrupt output if you get them wrong.

1. **Enrichment must stay idempotent.** Three guards: skip when the description
   already has `www.imdb.com/find?`, skip poster replacement when the `<img>` is
   already `image.tmdb.org`, skip when `EpGuides</b>` is present. Hourly CI means
   a broken guard accumulates duplicates fast.
2. **EpGuides fallback links are Google CSE URLs**, not `epguides.com` links
   (`www.google.com/cse?cx=…&q=…`). Grepping for `epguides.com` gives a false
   negative on "is this linked?".
3. **`allshows.txt` keys are space-stripped.** `_normalize_epguides_title()` keeps
   only `[a-z0-9]`, so the key for *The Gentlemen* is `thegentlemen`.
4. **Series-ness is `kind`, not `category`.** `showrss` is `category: torrents`,
   `kind: series`.
5. **Every category in `sites.yaml` needs a `CATEGORY_ENRICHMENT` entry**, or it
   silently gets `streaming`. `cinema` (14 sites) once fell through to an
   undispatched `catalog` mode; `releases` once got `{"mode": "none"}`.
6. **The aggressive ad set must not contain content containers** — see §6.7. The
   same reasoning applies to a site's `ad_selectors` in `sites.yaml`: use class or
   id selectors, never a bare tag. Two tests enforce it
   (`test_aggressive_set_contains_no_content_containers`,
   `test_site_selectors_are_compound_not_bare_tags`).
7. **`enabled: false` removes a site everywhere**: generation, enrichment, index,
   OPML, and the WebSub ping. `--site <disabled>` reports it as unmatched.
8. **`patch.object` on `enrich_feeds.FEEDS_DIR` does not affect a
   `runpy.run_module` execution** — the module is re-imported fresh. To sandbox the
   orchestrator in a test, copy feeds to a tmp dir and patch there, or patch the
   module the entry point actually imports.
9. **`MAX_DESCRIPTION_LENGTH` is 50 000 chars** and is enforced in two places
   (`article_enricher` and `ad_remover.truncate_content`). Descriptions of
   1.7k–35k chars are normal.
10. **Poster size constants live in `core/feed.py` only.** `fix_feeds.py` imports
    them; editing one and not the other causes the two stages to fight.
11. **`kind`/`enhance_mode`/`title_transform` are validated in `__post_init__`** —
    a bad value fails at config load, not at enrichment time.
12. **Unknown YAML keys raise.** That is intentional; don't "fix" it by ignoring
    them.
13. **Never commit generated output.** `feeds/`, `index.html`, `feeds.opml`,
    `.env`, `logs/` are gitignored; feeds ship as a Pages artifact.
14. **An extraction with no visible text must not be written.**
    `extract_main_content()` falls back to "clean the whole page" when its selector
    matches nothing, so an empty page yields a non-empty `<html>` shell that a
    truthiness check accepts. That used to overwrite a perfectly good RSS excerpt
    with an empty document. `MIN_BODY_TEXT = 200` guards it; a skipped item keeps
    the feed's own description and counts as `skipped`.
15. **`detail_article_selector` is for sites whose content wrapper repeats the
    title.** gabriel-ursan needs `.articol-continut` (its `<article>` wraps a
    `<header>` repeating the H1, share navs, a byline, prev/next links and the
    comment section); manafu needs `.entry` (its `.content` wrapper holds an `<h2>`
    title, byline, share buttons and a tags footer). In both cases the reader
    showed the headline twice and carried the theme chrome. Keep the class-scoped
    `nav`/`section`/`div` selectors as the fallback path; never use a bare tag.
16. **`truncate_content()` must drop everything after the cap, not just trim the one
    node that crosses it.** It used to `break` after editing a single text node, so
    a page dominated by one huge text node (a `<style>` block) stayed unbounded and
    the `MAX_DESCRIPTION_LENGTH` check rejected the item. This is what left hoinaru
    shipping 10/10 excerpts.
14. **The two reader gutters have opposite drag signs** (§9): sidebar `dir: +1`,
    panel `dir: -1`. Do not "simplify" them to one sign — that made the panel
    handle run ~300 px away from the cursor.
15. **Most `scripts/*.py` need `PYTHONPATH=.`** (or `python -m scripts.<name>`).
    Running `python scripts/foo.py` puts `scripts/` on `sys.path` instead of the
    repo root, so `from core.… import` dies with `ModuleNotFoundError: No module
    named 'core'`. `scripts/generate_feeds.py` is the exception — it fixes
    `sys.path` itself — and the Dockerfile `CMD` uses the module form.

---

## 14. Adding or changing a site

1. Edit `config/sites.yaml` (see `PROJECT.md` for selector discovery techniques).
2. `PYTHONPATH=. python3 -m core.cli generate --site <name>` to test one feed.
3. `PYTHONPATH=. python3 scripts/enrich_feeds.py` to check enrichment.
4. `PYTHONPATH=. python3 scripts/fix_feeds.py` and `generate_index.py`.
5. `PYTHONPATH=. python3 -m pytest tests/ -q` and `python3 -m ruff check .`.

If you introduce a **new category**, add it to `CATEGORY_ENRICHMENT` (gotcha 5).
If you add a site whose feed duplicates another, verify the overlap against real
generated output before setting `enabled: false` — check which feed is the
*superset* and keep that one.
