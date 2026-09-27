# CLAUDE.md

This repository's agent instructions live in [`AGENTS.md`](AGENTS.md), and the
authoritative architecture reference is [`docs/agent-guide.md`](docs/agent-guide.md).
Read both before changing anything.

Two rules that are not obvious from the code:

- **Always update `README.md`** when adding, removing, enabling, disabling, or
  changing any feature, feed, filter, enrichment behaviour, or workflow.
- **Never commit generated output.** `feeds/`, `index.html`, `feeds.opml`,
  `.env`, and `logs/` are gitignored on purpose; feeds ship as a Pages artifact.
