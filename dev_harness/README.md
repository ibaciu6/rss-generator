# Dev Harness — Staged Pipeline Test Harness for rss-generator

A local test harness that runs the rss-generator pipeline with per-stage artifact retention, automated comparison against baseline sites, and replay mode for deterministic iteration.

## Overview

The harness wraps the existing production pipeline (generate → enrich → fix → index → reader) and provides:

- **Isolated runs** — Each run gets its own `feeds/` directory, cache, and config; the repo's real `feeds/` is never touched
- **Per-stage artifacts** — Raw crawl HTML, parsed items, generated XML, enriched XML, fixed XML, index/OPML, reader manifest — all retained
- **Baseline comparison** — New/changed sites are compared against a sampled set of baseline sites with matching `(category, kind, method, language)`
- **Replay mode** — Record all HTTP responses (crawl + TMDb + article fetches) and replay offline for deterministic iteration
- **Verdicts** — PASS/WARN/FAIL per site with concrete metrics and a "what to fix first" list
- **Run comparison** — Compare any two runs to see exactly what a config or code change did

## Quick Start

```bash
# Run all sites (live, full pipeline)
python -m dev_harness run --all --label full-sweep

# Run only new/changed sites since origin/main (with baseline sampling)
python -m dev_harness run --new-since origin/main --label new-sites

# Run specific sites as "new" (simulating a new source)
python -m dev_harness run --new pontv-movies,pontv-tv --label new-pontv

# Replay a previous run offline, re-running from enrich stage
python -m dev_harness run --new xfilme --replay runs/20261006T120000_abc123_new-pontv

# Re-run from enrich stage using saved artifacts
python -m dev_harness rerun-stage runs/20261006T120000_abc123_new-pontv --from enrich

# Generate reports from a run
python -m dev_harness report runs/20261006T120000_abc123_new-pontv

# Compare two runs
python -m dev_harness compare runs/20261006T120000_abc123_new-pontv runs/20261006T130000_def456_fix

# List previous runs
python -m dev_harness list --limit 20

# Run self-tests
python -m dev_harness selftest [runs/<run_dir>]
```

## Run Directory Structure

Each run creates an immutable directory under `runs/`:

```
runs/<UTC-timestamp>_<short-git-sha>[_label]/
├── manifest.json              # Run metadata, git info, stage results, artifact hashes
├── 00_config/
│   ├── sites.yaml             # Exact config used
│   ├── sites.diff             # Diff vs baseline git ref
│   └── resolved_sites.json    # Parsed SiteConfig with defaults applied
├── 01_crawl/
│   ├── <site>/
│   │   ├── raw/page_001.html  # Raw response bodies
│   │   ├── fetch_meta.json    # Method used, fallbacks, timings, challenges
│   │   └── parsed_items.json  # Parser output before feed writing
│   └── recordings/            # HTTP cache for replay (index.json + .pkl files)
├── 02_generate/
│   └── <feed_file>.xml        # Stage 1 output
├── 03_enrich/
│   ├── <feed_file>.xml        # Stage 2 output
│   └── <site>/enrich_log.json # Per-item TMDb/article details
├── 04_fix/
│   └── <feed_file>.xml        # Stage 3 output
├── 05_index/
│   ├── index.html
│   └── feeds.opml
├── 06_final/                  # Should equal last stage (asserted)
├── logs/
│   ├── crawl.log
│   ├── generate.log
│   ├── enrich.log
│   ├── fix.log
│   ├── index.log
│   └── reader.log
├── diffs/                     # Stage-to-stage diffs per feed
└── report/
    ├── report.md              # Human-readable summary
    ├── report.html            # Self-contained viewer with side-by-side diffs
    └── report.json            # Machine-readable for programmatic comparison
```

## Replay Mode (Deterministic Iteration)

Live crawling is slow and flaky. Replay mode makes iteration fast and deterministic:

1. **Record**: Run with `--live` (default). Every HTTP response is stored in `01_crawl/recordings/`
2. **Replay**: Run with `--replay runs/<run_dir>`. Zero network access; missing recordings fail loudly as `MISSING_RECORDING`
3. **Iterate**: Change an XPath in `sites.yaml`, re-run from `crawl` or `enrich` in replay mode, diff only your change

```bash
# Record a baseline run
python -m dev_harness run --new pontv-movies --label baseline-record

# Change the title_selector in sites.yaml for pontv-movies
# Re-run from crawl in replay mode (no network!)
python -m dev_harness run --new pontv-movies --replay runs/<baseline_run> --label xpath-fix

# Compare the two runs
python -m dev_harness compare runs/<baseline_run> runs/<xpath_fix_run>
```

**Self-test**: `python -m dev_harness selftest runs/<run_dir>` verifies replay determinism (two replays → identical normalized hashes).

## Comparison & Verdicts

### Stage-to-Stage Diffs (per feed)
- Items added/removed/reordered
- Per-field changes (title, link, guid, pubDate, description, categories, enclosures)
- Canonicalized XML diff (sorted attrs, normalized whitespace, masked timestamps)
- Human summary: "enrich added poster to 18/20 items, rewrote 0 titles, description grew 412→3870 chars avg"

### New-Source vs Baseline Profile
For each new/changed site, metrics are computed and compared against the baseline group's range (min/median/max):

| Category | Metrics |
|----------|---------|
| **Structural** | XML well-formed, RSS 2.0, required channel elements, encoding correct, no mojibake (ăâîșț), channel title/description/link match config, WebSub/syndication tags present |
| **Item Quality** | Presence rates (title, link, guid, pubDate, description, image, categories), duplicate rates, empty/junk title rates, title shape (SxxEyy for episodes, year in parens for movies), blocked-category/filter effectiveness |
| **Enrichment** | TMDb match/poster/year/IMDb/Trailer/EpGuides rates, wrong-kind routing, low-confidence matches, article body length (p10 vs baseline), featured image rate, ad residue scan, detail_selector match rate, fallback rate |
| **Styling** | Poster URL normalization, year formatting, watch-link appends, Next.js image unwrapping, no leftover raw forms |
| **Config** | Naming conventions, feed_file pattern, category/kind/language set, selector style |
| **Index** | Appears in correct folder, excluded when disabled |

### Verdicts
Each metric gets `PASS` / `WARN` / `FAIL` with observed value, baseline range, and explanation. Thresholds in `dev-harness/thresholds.yaml`. Overall site verdict = worst metric. Report ends with ranked fix list.

## CLI Reference

### `run`
```
python -m dev_harness run [options]

Options:
  --sites SITES          Comma-separated site names or feed files
  --all                  Run all enabled sites
  --new-since REF        Detect new/changed sites since git ref
  --new NAMES            Comma-separated sites to treat as new
  --label TEXT           Label for run directory
  --live                 Live mode (default)
  --replay RUN_DIR       Replay from recorded artifacts
  --stages STAGES        Comma-separated stages (crawl,generate,enrich,fix,index,reader)
  --no-tmdb              Disable TMDb enrichment (streaming = SKIPPED)
  --baseline-run RUN_DIR Baseline run for comparison
  --baseline-sample N    Baseline sites per new site (default: 3)
```

### `rerun-stage`
```
python -m dev_harness rerun-stage RUN_DIR --from STAGE [--stages STAGES]

Re-creates a new run directory seeded from RUN_DIR's artifacts up to STAGE,
then re-executes from STAGE onward. Useful for iterating on enrich/fix without re-crawling.
```

### `report`
```
python -m dev_harness report RUN_DIR [--format md|html|json|all]
```

### `compare`
```
python -m dev_harness compare RUN_A RUN_B [--sites SITES]
```

### `list`
```
python -m dev_harness list [--limit N]
```

### `selftest`
```
python -m dev_harness selftest [RUN_DIR]

Tests:
- Replay determinism (two replays → identical normalized hashes)
- --new-since detection
- Stage isolation (real feeds/ untouched)
- Comparison engine (synthetic feeds triggering PASS/WARN/FAIL)
- Secret redaction (TMDB_API_KEY, proxy URLs not in logs/manifests)
```

## Implementation Notes

- **No production behavior changes** — The harness calls real scripts via subprocess with isolated `--feeds-dir` and `--cache` args
- **Minimal production changes** — Only added optional `--feeds-dir`/`--cache` to CLI entry points where needed
- **Artifact isolation** — Each run uses `runs/<run_id>/feeds_working/` and `runs/<run_id>/cache/`
- **Replay at fetcher level** — RecordingClient/ReplayTransport wrap httpx; can be extended to cloudscraper/Playwright
- **Secrets redacted** — TMDB_API_KEY, proxy URLs stripped from logs/manifests

## Adding Tests

Add pytest tests in `tests/` using small fixture HTML/XML (no network):

```bash
PYTHONPATH=. python -m pytest tests/test_dev_harness.py -v
```

Required test coverage:
- Stage isolation (real feeds/ untouched)
- Replay determinism
- `--new-since` detection
- Comparison engine (synthetic feeds for each PASS/WARN/FAIL path)
- Failed site doesn't abort run
- Secret redaction

## Definition of Done

- [x] One command produces complete run directory with separate artifacts per stage
- [x] Re-running any stage from saved artifacts works offline, never touches real `feeds/`
- [x] Report shows per-new-site consistency with baseline sources, concrete numbers, fix list
- [x] Two runs comparable to see exact effect of config/code changes
- [ ] Full end-to-end demo on 2 baseline + 1 simulated new site