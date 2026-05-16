# Contributing

Thanks for your interest! This project is a config‑driven RSS feed generator for streaming sites.

## How to contribute

### Reporting bugs

Open an issue with:
- The feed or site affected
- What you expected vs what happened
- Any relevant logs or error messages

### Adding a new site

1. Check `config/sites.yaml` to understand the format
2. Use the onboarding script: `PYTHONPATH=. python scripts/onboard_site.py`
3. Or manually add the entry following existing patterns
4. Rebuild derived files: `PYTHONPATH=. python scripts/generate_index.py`
5. Submit a PR

See [`PROJECT.md`](PROJECT.md) for full instructions on adding sites, SPA scraping strategies, and architecture details.

### Removing a site

1. Remove the entry from `config/sites.yaml`
2. Remove the corresponding `<outline>` from `feeds.opml`
3. Remove the `<tr>` row from `index.html`
4. Delete the generated `feeds/<feed_file>.xml`
5. Submit a PR

### Code changes

- Python 3.11+, type annotations required
- Run tests: `PYTHONPATH=. python3 -m pytest tests/`
- Structured logging via structlog (JSON output in CI)
- Keep `config/sites.yaml` as the single source of truth — no hardcoded site lists

### Pull requests

- Keep changes focused — one PR per feature/fix
- Update docs if changing config schema or workflow
- Ensure the GitHub Actions workflow passes
