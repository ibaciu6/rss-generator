# CLAUDE.md

This repository's agent instructions live in [`AGENTS.md`](AGENTS.md), and the
authoritative architecture reference is [`docs/agent-guide.md`](docs/agent-guide.md).
Read both before changing anything.

Two rules that are not obvious from the code:

- **Always update `README.md`** when adding, removing, enabling, disabling, or
  changing any feature, feed, filter, enrichment behaviour, or workflow.
- **Never commit generated output by hand.** `feeds/`, `index.html`,
  `feeds.opml`, `.env`, and `logs/` are gitignored. The `update.yml` workflow is
  the only writer: it regenerates `feeds/` in place (updating the committed
  copies) and commits `feeds/*.xml` + `feeds.opml` to `main` (so
  `raw.githubusercontent.com` serves them), and deploys the rest to Pages.
