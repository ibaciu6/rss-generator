# RSS Generator — Project Rules

## Keep derived files in sync with config/sites.yaml

Whenever you add or remove a site entry in `config/sites.yaml`, you **must** update all of the following files to match:

1. **`feeds.opml`** — Add or remove the corresponding `<outline>` element inside the correct group.
2. **`index.html`** — Add or remove the corresponding `<tr>` row in the status table.
3. **`feeds/<feed_file>.xml`** — When **removing** a site, delete the generated feed XML file. When **adding** a site, the next workflow run generates it.

Do not commit a `config/sites.yaml` change unless the other three files are consistent.

## References

- **Config**: `config/sites.yaml` — master list of all sites with selectors, method, category, etc.
- **Feeds OPML**: `feeds.opml` — OPML index for Inoreader import.
- **Index page**: `index.html` — status table of all feeds.
- **Generated feeds**: `feeds/*.xml` — per-site RSS output.
- **Workflow**: `.github/workflows/update.yml` — CI/CD: generate, enrich, deploy to Pages.
- **Core engine**: `core/engine.py`, `core/config.py`, `core/cli.py`.
