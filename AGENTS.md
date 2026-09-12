# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11+ RSS/Atom generator for streaming sites. Keep site-specific behavior declarative in `config/sites.yaml`, the repository’s source of truth. Core orchestration and models live in `core/`; HTTP/browser fetching and XPath parsing live in `scraper/`; executable maintenance and generation scripts live in `scripts/`. Tests mirror those areas in `tests/`. Generated feed XML is written under `feeds/`; `index.html` and `feeds.opml` are derived artifacts.

## Build, Test, and Development Commands

Create a virtual environment, install dependencies, then install the Playwright browser:

```bash
python -m venv .venv
pip install -r requirements.txt
python -m playwright install chromium
```

Run the generator with `PYTHONPATH=. python scripts/generate_feeds.py` (or `python -m core.cli generate`). Run the complete local pipeline with `generate_feeds.py`, `enrich_posters.py`, `fix_feeds.py`, then `generate_index.py`. Start the static preview with `./scripts/serve.sh`; use `./scripts/start_reader.sh` to review generated feeds locally.

## Coding Style & Naming Conventions

Use four-space indentation, Python type annotations, and docstrings where they clarify public behavior. Prefer small, testable functions and preserve the existing modular split between `core` and `scraper`. Use `snake_case` for modules, functions, variables, and YAML keys; use `PascalCase` for classes (for example, `SiteConfig`). Do not hardcode site lists in Python—extend `config/sites.yaml` following existing entries. No formatter or linter is configured; match nearby code.

## Testing Guidelines

Tests use pytest and are discovered from `tests/`. Name files `test_<area>.py` and tests `test_<behavior>`. Run the suite before submitting:

```bash
PYTHONPATH=. python -m pytest tests/
```

Add focused coverage for parser, fetcher, config, or feed behavior you change. Avoid tests that depend on live streaming sites unless explicitly needed.

## Commit & Pull Request Guidelines

Follow the repository’s conventional, scoped commit style: `feat(index): ...`, `fix(fetcher): ...`, or `refactor(opml): ...`; keep the subject imperative and concise. Keep each PR focused, describe the user-visible effect, link an issue when applicable, and update documentation for schema or workflow changes. For site changes, update `config/sites.yaml`, regenerate `index.html` and `feeds.opml`, and confirm the test suite passes. Do not commit secrets; put local credentials such as `TMDB_API_KEY` in the ignored `.env` file.
