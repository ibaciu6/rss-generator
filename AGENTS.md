# Repository Guidelines

## Orientation

Read [`docs/agent-guide.md`](docs/agent-guide.md) first. It is the authoritative
description of what the project does, how the four pipeline stages work
internally, and the invariants that silently corrupt output when violated
(enrichment idempotency, `kind` vs `category` routing, the aggressive ad-selector
constraint, the reader gutter sign). `PROJECT.md` is the site-authoring handbook.

## Project Structure & Module Organization

This is a Python 3.11+ RSS/Atom generator for streaming sites. Keep site-specific behavior declarative in `config/sites.yaml`, the repository’s source of truth. Core orchestration and models live in `core/`; HTTP/browser fetching and XPath parsing live in `scraper/`; executable maintenance and generation scripts live in `scripts/`. Tests mirror those areas in `tests/`. Generated feed XML is written under `feeds/`; `index.html` and `feeds.opml` are derived artifacts.

## Build, Test, and Development Commands

Create a virtual environment, install dependencies, then install the Playwright browser:

```bash
python -m venv .venv
pip install -r requirements.txt
python -m playwright install chromium
```

Run the generator with `PYTHONPATH=. python scripts/generate_feeds.py` (or `python -m core.cli generate`, optionally `--site <name>` to regenerate a single feed). Run the complete local pipeline with `generate_feeds.py`, `enrich_feeds.py`, `fix_feeds.py`, then `generate_index.py`. Start the static preview with `./scripts/serve.sh`; use `./scripts/start_reader.sh` to review generated feeds locally.

## Coding Style & Naming Conventions

Use four-space indentation, Python type annotations, and docstrings where they clarify public behavior. Prefer small, testable functions and preserve the existing modular split between `core` and `scraper`. Use `snake_case` for modules, functions, variables, and YAML keys; use `PascalCase` for classes (for example, `SiteConfig`). Do not hardcode site lists in Python—extend `config/sites.yaml` following existing entries. No formatter or linter is configured; match nearby code.

## Git Workflow

- **Push directly to `main`. Keep exactly one branch in the repository.**
  Do not open pull requests and do not create feature branches: commit to
  `main` and `git push origin main`. If a branch is ever created, delete it
  (local and remote) so `main` is the only branch.
- `main` also receives the hourly `chore(feeds)` commits, so it can move under
  you: `git fetch origin` and rebase your commit onto `origin/main` before
  pushing. Those commits touch only `feeds/`, so the rebase is normally clean.
- **ALWAYS monitor GitHub Actions after pushing.** A push to `main` triggers
  `lint.yml` (ruff), `secret-scan.yml` (gitleaks), CodeQL, and the long
  `update.yml` generate/enrich/publish run. Immediately check the latest runs
  (`gh run list --branch main --limit 5`) and follow up on any failure (view
  logs, fix, push again). Never leave failing CI unattended.
- **Watch the CodeQL comments and fix what they find.** CodeQL alerts that are
  genuinely test-only can be dismissed as `used in tests` with a reason; do not
  contort a test to satisfy the rule. A failing CodeQL check may reflect an
  alert already on `main`, not one you introduced — GitHub counts any alert in
  a file the change touches — so check the alert's `most_recent_instance.ref`
  before re-fixing code that is already fixed.
- **Fix a finding by removing the construct, not by proving it safe.** The
  reader's `?feed=` handler built a path from the query string and then proved
  containment; the fix was an allowlist, because a name that is not one of the
  files in `feeds/` cannot name a file outside it.

## Testing Guidelines

Tests use pytest and are discovered from `tests/`. Name files `test_<area>.py` and tests `test_<behavior>`. Run the suite before submitting:

```bash
PYTHONPATH=. python -m pytest tests/
```

Add focused coverage for parser, fetcher, config, or feed behavior you change. Avoid tests that depend on live streaming sites unless explicitly needed.

## Commit Guidelines

Follow the repository’s conventional, scoped commit style: `feat(index): ...`, `fix(fetcher): ...`, or `refactor(opml): ...`; keep the subject imperative and concise. Keep each commit focused, describe the user-visible effect, link an issue when applicable, and update documentation for schema or workflow changes. For site changes, update `config/sites.yaml` and confirm the test suite passes; do not commit generated output — the `update.yml` workflow produces and commits the published feeds, `index.html` and `feeds.opml`. Do not commit secrets; put local credentials such as `TMDB_API_KEY` in the ignored `.env` file.
