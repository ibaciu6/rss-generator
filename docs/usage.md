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

- GitHub Actions workflow (`.github/workflows/update.yml`) runs every 2 hours and on manual dispatch.
- It:
  - installs Python and dependencies
  - installs Playwright browsers
  - runs `python scripts/generate_feeds.py`
  - commits changes under `feeds/` back to the repository
- GitHub Pages can be configured to serve from:
  - Branch: `main`
  - Folder: `/feeds`

Public feed URLs will look like:

- `https://ibaciu6.github.io/rss-generator/sitefilme.xml`

