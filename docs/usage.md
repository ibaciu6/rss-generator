## Usage

### Configuring sites

Sites are defined in `config/sites.yaml`:

```yaml
sites:
  sitefilme:
    url: "https://sitefilme.com/"
    method: "playwright"          # http | cloudscraper | playwright

    item_selector: "//article"
    title_selector: ".//h2/a/text()"
    link_selector: ".//h2/a/@href"
    description_selector: ".//p/text()"
    date_selector: ""             # optional, XPath to date text

    feed_file: "sitefilme.xml"    # output file in feeds/
    category: "movies"            # optional
    fallback_urls: []             # optional extra URLs to try
    allowed_final_hosts: []       # optional redirect allowlist
    blocked_final_hosts: []       # optional redirect denylist
    blocked_content_markers: []   # optional strings that invalidate a page
    allow_empty_title: false      # allow detail-page title enrichment
    detail_method: "http"         # optional method for detail pages
    detail_title_selector: ""     # optional XPath for title from detail page
    detail_description_selector: ""  # optional XPath for description from detail page
    max_items: 50                 # optional cap after parsing/dedup
```

### Running the generator

From the project root:

```bash
python -m core.cli generate
# or
python scripts/generate_feeds.py
```

Generated feeds are written to `feeds/`:

- `feeds/sitefilme.xml` (RSS 2.0)
- `feeds/sitefilme.atom.xml` (Atom)

### Adding a new site

1. Edit `config/sites.yaml`.
2. Add a new entry under `sites:` with:
   - `url`
   - `method` (one of `http`, `cloudscraper`, `playwright`)
   - `item_selector`
   - `title_selector`
   - `link_selector`
   - optional `description_selector`, `date_selector`, `category`
   - `feed_file`
3. Run `python -m core.cli generate`.
4. Commit updated feeds if desired.

### GitHub Actions & Pages

- GitHub Actions workflow (`.github/workflows/update.yml`) runs every 5 minutes and on manual dispatch.
- It:
  - installs Python and dependencies
  - installs Playwright browsers
  - runs `python scripts/generate_feeds.py`
  - commits changes under `feeds/` back to the repository
- GitHub Pages can be configured to serve from:
  - Branch: `main`
  - Folder: `/(root)`

Public feed URLs will look like:

- `https://ibaciu6.github.io/rss-generator/feeds/sitefilme.xml`

### Sites that redirect by region or language

Some domains (e.g. sitefilme.com) serve different content to automated requests or by IP/language—for example a Chinese portal instead of the intended site. The fetcher avoids this by:

- Sending browser-like headers (Chrome User-Agent, `Accept-Language: en-US,en`) on all strategies.
- Using Playwright with `locale="en-US"` when that method is used.

If you still get the wrong version, use `method: "playwright"` for that site so the request comes from a full browser context.

If the source is geo-blocked or requires a residential IP, set `RSS_GENERATOR_PROXY_URL` before running the generator. The proxy is applied to `httpx`, `cloudscraper`, and Playwright.

When every URL candidate fails validation or returns a challenge/error page, the generator removes that site's RSS/Atom files instead of publishing an empty feed. The index page then shows that source as unavailable.
