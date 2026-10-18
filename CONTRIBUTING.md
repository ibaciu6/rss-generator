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
5. Commit to `main` and push, after the review below

See [`PROJECT.md`](PROJECT.md) for full instructions on adding sites, SPA scraping strategies, and architecture details.

### Removing a site

1. Remove the entry from `config/sites.yaml`
2. Remove the corresponding `<outline>` from `feeds.opml`
3. Remove the `<tr>` row from `index.html`
4. Delete the generated `feeds/<feed_file>.xml`
5. Commit to `main` and push, after the review below

### Code changes

- Python 3.11+, type annotations required
- Run tests: `PYTHONPATH=. python3 -m pytest tests/`
- Structured logging via structlog (JSON output in CI)
- Keep `config/sites.yaml` as the single source of truth — no hardcoded site lists

### Code review (mandatory)

Every push is reviewed first, against the exact commits being pushed
(`git diff origin/main...HEAD`). The review skill is vendored at
`.agents/skills/talos-code-reviewer/SKILL.md`; apply its five checks —
architecture, security, idiomaticity, testing, documentation — noting two
repository-specific rules: site-specific behavior belongs in
`config/sites.yaml`, and a stage that rewrites generated HTML must be a no-op on
HTML it has already rewritten.

Fix what the review finds, then record it: each pushed commit carries a
`Reviewed-by:` trailer. `.githooks/pre-push` enforces the trailer and runs the
mechanical gates (ruff + tests) before the push leaves your machine; enable it
once per clone with `git config core.hooksPath .githooks`. Use
`SKIP_CODE_REVIEW=1` only to bypass it deliberately.

### Commits and pushes

- Keep changes focused — one logical change per commit
- Update docs if changing config schema or workflow
- Push directly to `main`; this repository has exactly one branch and does not
  use pull requests

