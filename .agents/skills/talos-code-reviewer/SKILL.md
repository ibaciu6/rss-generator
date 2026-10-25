---
name: talos-code-reviewer
description: Use when working inside this repository (rss-generator). Triggers on project-scoped phrases: "talos review", "talos code review", "use talos", "invoke talos", "review with talos", "rss-generator review", "talos skill". For a general code review that does not need project context, use ECC's /code-review command instead.
---

# Talos Code Reviewer

**Trigger phrases** (project-scoped — invoke this skill with these):
  talos review, talos code review, use talos, invoke talos reviewer,
  review with talos, rss-generator review, talos skill, talos-check

**When to use this vs /code-review:**
  - **This skill** — you are inside rss-generator and want a review that knows
    about config/sites.yaml conventions, the §13 pipeline invariants, the
    no-branches / direct-to-main workflow, and the two hard rules: site
    behaviour lives in sites.yaml (never in Python), deploy-stage idempotency.
  - **ECC /code-review** — general quality/security review that works across
    any repo, posts results to .claude/reviews/, and knows nothing about
    rss-generator-specific conventions.

Use this skill to ensure high code quality, consistency, and compliance with
the protocol's core mandates.

Load these first:
- `AGENTS.md`
- `CONTRIBUTING.md`
- `.agent/planning/program-anchor-index.md`

Workflow:
1. **Architecture:** Verify the change aligns with `talos-contract-first`.
   Check for cross-repo leaks or deep imports.
2. **Security:** Check for secret leakage, unsafe defaults, and authz-sensitive
   holes. Use `talos-capability-audit` if needed.
3. **Idiomaticity:** Ensure Python follows PEP 8 and TS/JS follows the site's
   lint/format rules. Check for consistency with the existing codebase.
4. **Testing:** Verify the change is covered by tests and follows
   `talos-tdd-workflow`. Run `deploy/scripts/run_all_tests.sh --changed`.
5. **Documentation:** Ensure all changed public APIs or CLI flags are documented.

Non-negotiables:
- No deep cross-repo imports.
- No hardcoded secrets or absolute paths.
- No change without tests (negative cases included for security-critical logic).
- No undocumented breaking changes or CLI flags.

Done checklist:
- Code reviewed for architecture, security, and idiomaticity.
- Tests verified and green.
- Documentation parity checked.
- PR description draft includes a clear summary and impact analysis.
- Final review summary lists findings clearly with actionable feedback.

## Notes for non-Talos repositories

The referenced files/paths (`talos-contract-first`, `.agent/planning/…`,
`deploy/scripts/run_all_tests.sh`, `talos-tdd-workflow`, `talos-capability-audit`)
belong to the Talos protocol repo. When applying this skill elsewhere, map them
to the local equivalents:

- `AGENTS.md` / `CONTRIBUTING.md` → this repo's own contributor docs.
- `deploy/scripts/run_all_tests.sh --changed` → the repo's changed-files test
  command (e.g. `PYTHONPATH=. python -m pytest tests/`).
- `talos-capability-audit` / `talos-tdd-workflow` → this repo's security and
  TDD skills, if any.

## This repository

- Contributor docs: `AGENTS.md` (rules), `CONTRIBUTING.md` (how to change a
  site), `PROJECT.md` (site-authoring handbook). The pipeline invariants that
  silently corrupt output are in `docs/agent-guide.md` §13 — read the ones that
  cover the code you touched before calling a review done.
- Lint: `.venv/bin/python -m ruff check .`
- Tests: `PYTHONPATH=. .venv/bin/python -m pytest tests/`, or a single file
  while iterating.
- The review is mandatory before every push and ran against
  `git diff origin/main...HEAD`; the checklist above and the pre-push gate are
  described in `AGENTS.md` § *Code Review*.
- Two repository-specific rules that a generic review misses:
  - site-specific behavior belongs in `config/sites.yaml`, never in Python;
  - a stage that rewrites generated HTML must be a no-op on HTML it has already
    rewritten, or the deploy moves on every run.
