# Dev Harness Run Report: 20261006T153415Z_76a5e95b_test-pontv-full

**Timestamp:** 20261006T153415Z
**Git SHA:** 76a5e95be0ac427101303e92e3feca15c36fe389 (dirty)
**Command:** `/mnt/d/Download/tools/rss-generator/dev_harness/__main__.py run --new pontv-movies --stages crawl,generate,enrich,fix --label test-pontv-full`
**TMDb Enabled:** True
**Replay Mode:** False

## Sites Selected

- pontv-movies 🆕

## Stage Results

| Stage | Status | Duration | Details |
|-------|--------|----------|---------|
| crawl | ✅ ok | 6.52s | {'sites': {'pontv-movies': {'status': 'ok', 'items': 'unknown'}}} |
| generate | ✅ ok | 7.6s | {'feeds_generated': 1, 'feeds_failed': 0, 'exit_code': 0} |
| enrich | ✅ ok | 0.96s | {'feeds_enriched': 0, 'feeds_skipped': 0, 'exit_code': 0} |
| fix | ✅ ok | 1.1s | {'exit_code': 0} |

## Feed Artifacts

### generate (1 feeds)

- `pontv-movies.xml`

### enrich (1 feeds)

- `pontv-movies.xml`

### fix (1 feeds)

- `pontv-movies.xml`

## Feed Quality (post-fix)

| Feed | Items | Status |
|------|-------|--------|

## Artifact Hashes

```json
{
  "00_config/sites.yaml": "04278285c96caf126f206309ca2bad0ab4526b9eec41b30f61de1b3bf37255fd",
  "01_generate/pontv-movies.xml": "2d72871e43e936443246497a9e20d220b5b5671485afafc034fd295b6f5d6ba2",
  "02_enrich/pontv-movies.xml": "2d72871e43e936443246497a9e20d220b5b5671485afafc034fd295b6f5d6ba2",
  "03_fix/pontv-movies.xml": "2d72871e43e936443246497a9e20d220b5b5671485afafc034fd295b6f5d6ba2",
  "cache/cache.json": "0c67e8faee812751ce4f6003e438f67e6bbc0f7a14b19e034208d9a4a320bb5c",
  "feeds_working/pontv-movies.xml": "2d72871e43e936443246497a9e20d220b5b5671485afafc034fd295b6f5d6ba2",
  "logs/enrich.log": "50bbb8dcd29916a09cbc0276e59415d474931a0295797749acc2749c9538eec5",
  "logs/fix.log": "5ac8897096885bc381494ad20387db5319fd821ef8a856bba830f706191beb72",
  "logs/generate.log": "67270bda9aad1aaa5e58117806af6addee3dcc86ba1cde29578642d8826be185",
  "01_crawl/recordings/index.json": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
}
```