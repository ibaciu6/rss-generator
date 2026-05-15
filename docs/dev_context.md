# RSS Generator Development & Stabilization Plan

## 1. Project Vision
Build a generic, cloud-friendly RSS generator platform (similar to PolitePol) that converts arbitrary websites into high-quality RSS feeds. Focus on fetching the latest updates from homepages with rich metadata (posters, trailers, IMDb).

## 2. Site Build Information (Root Domains)

### seriale-online.net
- **URL**: `https://seriale-online.net/`
- **Structure**: Uses `<article>` tags for items. 
- **Selectors**:
    - Item: `//article`
    - Title: Extract from `img/@title`, removing suffixes like "Online Subttirat - FSonline" or handling "Sezonul/Episodul" parts.
    - Image: `img/@src`
    - Link: `a/@href`

### xcinema.ro
- **URL**: `https://www.xcinema.ro/`
- **Structure**: Uses `<article class="movie-wrapper">` for items.
- **Selectors**:
    - Item: `//article[contains(@class,'movie-wrapper')]`
    - Title: Extract from `h3` or `@title`.
    - Image: `.//img/@src` (relative to root).
    - Link: `.//a/@href` (relative to root).

### xfilme.ro
- **URL**: `https://xfilme.ro/`
- **Structure**: Uses `<article class="item movies">` or similar inside specific sections.
- **Selectors**:
    - Item: `//article[contains(@class,"item")]`
    - Title: `.//h3/a/text()`
    - Image: `.//div[@class="poster"]/img/@src`
    - Link: `.//h3/a/@href`

## 3. Technical Roadmap

### Phase 1: Local Environment Stabilization
- **Goal**: Ensure the project can be developed and tested reliably on WSL2 (Ubuntu).
- **Actions**:
    - Fixed `PYTHONPATH` and module resolution issues.
    - Standardized `.venv` creation and dependency management.
    - Implemented structured logging for better debugging.
    - Created a "safe" local test runner (`test_runner.sh`) with timeouts and log capture.
    - **XPath 2.0**: Integrated `elementpath` for complex selector logic.

### Phase 2: Simplified Homepage Scraping
- **Goal**: Simplify feeds to fetch from homepages only.
- **Actions**:
    - **Consolidated Config**: Update `config/sites.yaml` to use root URLs.
    - **Advanced Selectors**: Implement robust XPath 2.0 expressions to handle mixed content on homepages.
    - **Rich Content**: Standardize `<description>` format with images, YouTube search links, and IMDb search links.

### Phase 3: Robust Scraping Strategies
- **Goal**: Bypass geo-restrictions and Cloudflare reliably.
- **Actions**:
    - Refine fallback logic: `httpx` -> `cloudscraper` -> `Playwright`.
    - Handle relative URLs and redirects automatically.

### Phase 4: CI/CD & Deployment Optimization
- **Goal**: Reliable automated updates on a cadence compatible with major readers (e.g. Inoreader ~hourly polling per [feed-fetcher](https://www.inoreader.com/feed-fetcher); RSS `ttl` and WebSub support that model).
- **Actions**:
    - Update `.github/workflows/update.yml` with optimized schedule.
    - Use `git pull --rebase` to avoid conflict errors in CI.

## 4. Execution Rules
- **Persistence**: Work until all feeds are healthy.
- **Monitoring**: Always check command outputs; never assume success.
- **Verification**: Post-push verification of the actual public XML content.

---

## Session Update — 2026-05-15

### Goal
Implement RSS feeds for all viable FMHY streaming sites; generate valid RSS with poster + Trailer + IMDb links.

### Constraints
- Skip sites requiring sign-up, VPN, or geo-limited to US/UK/CA/AU
- Prefer raw HTTP > cloudscraper > Playwright sites
- Use dedicated listing pages over homepages when possible
- Description selectors must include poster image + YouTube Trailer + IMDb search links

### Completed
- **Fixed StreamGoblin Movies**: changed `item_selector` from `/player/movie/` to `/movie/ID`; 20 items
- **Fixed Stigstream Movies/TV**: switched from Playwright to raw HTTP with `//div[contains(@class,'flex-col')]` parsing hidden RSC divs; 20 items each
- **Fixed GGFlix Movies**: corrected URL→`ggflix.live/movies`, wait selector uses `/movie/` not `/movies/`; 20 items
- **Fixed GGFlix Movies+Series**: stripped "Poster for " prefix via `substring-after()`; clean titles
- **Removed AlienFlix** (redirects to hdtodayz.net)
- **Added 12 verified new feeds**:
  - HDTodayz Movies (HTTP, aria-label pattern, 80→30 items)
  - HDTodayz Series (HTTP, aria-label pattern, 80→30 items)
  - BingeBox Movies (HTTP, media-card a tag, 51→30 items)
  - BingeBox TV (HTTP, media-card a tag /show/, 29→29 items)
  - UniqueStream Movies (HTTP, WP article pattern, 23→23 items)
  - UniqueStream TV (HTTP, WP article pattern, 3→3 items)
  - Movish Movies (HTTP, `div.group.overflow-hidden`, 56→30 items via `img/@data-src`)
  - FshareTV Movies (HTTP, `div.movie-card`, 84→30 items)
  - FlickyStream Movies (Playwright, `a.group\/card` + `/movie/`, 90→30 items)
  - FlickyStream TV (Playwright, `a.group\/card` + `/tv/`, 71→30 items)
  - ONOFLIX Movies (Playwright, `a[@aria-label and href=/movie/]`, 32→27 items)
  - Cinezo Movies (Playwright, `div.swiper-slide`, 12→12 items)
- Batch-tested all remaining FMHY candidates (33 sites) with Playwright to discover card patterns
- Only 8 of 33 had usable content after Playwright rendering; 4 of those 8 had selectors that worked reliably in the generator (FlickyStream, ONOFLIX, Cinezo)
- 4 of 8 failed: bCine (too slow async render, wait selector times out), MeowTV (same), ONOFLIX TV (/tv endpoint has no content), Bingeflix (items found in PW but generator fails)
- **Total: 49 configs → 48 working feeds** (only `f_hdonline` fails — DNS resolution, site appears permanently down)
- Written 49 RSS feeds to `feeds/*.xml`

### Blocked
- **bCine** (bcine.app) — fully async SPA, movie links load after 20s+
- **MeowTV** (meowtv.ru) — same async slow render issue
- **ONOFLIX TV** (onoflix.ru/tv) — /tv endpoint has no content
- **Bingeflix** (bingeflix.tv) — 200 movie links found in PW but generator fails on content marker
- **PopcornMovies / 67Movies** — Cloudflare challenge
- **~25 other RSC/SPA sites** — 0 usable items in raw HTML or PW render

### Key Decisions
- **AlienFlix → hdtodayz.net**: follow redirect; same aria-label engine as CinebyTV
- **Stigstream to HTTP**: raw HTML has all data in hidden RSC divs; lxml parses without Playwright
- **GGFlix `/movie/` (singular)**: wait selector and link pattern uses `/movie/ID` not `/movies/ID`
- **Movish uses `img/@data-src`**: Livewire lazy-loads posters; @src is empty, @data-src has the URL
- **All new Playwright sites add ~15s each**; 6 PW sites run in ~90s total
