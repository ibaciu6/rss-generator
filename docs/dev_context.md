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
- **Goal**: Reliable automated updates every 2 minutes.
- **Actions**:
    - Update `.github/workflows/update.yml` with optimized schedule.
    - Use `git pull --rebase` to avoid conflict errors in CI.

## 4. Execution Rules
- **Persistence**: Work until all feeds are healthy.
- **Monitoring**: Always check command outputs; never assume success.
- **Verification**: Post-push verification of the actual public XML content.
