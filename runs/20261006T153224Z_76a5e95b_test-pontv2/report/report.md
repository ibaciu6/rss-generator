# Dev Harness Run Report: 20261006T153224Z_76a5e95b_test-pontv2

**Timestamp:** 20261006T153224Z
**Git SHA:** 76a5e95be0ac427101303e92e3feca15c36fe389 (dirty)
**Command:** `/mnt/d/Download/tools/rss-generator/dev_harness/__main__.py run --new pontv-movies --stages crawl,generate --label test-pontv2`
**TMDb Enabled:** True
**Replay Mode:** False

## Sites Selected

- pontv-movies 🆕

## Stage Results

| Stage | Status | Duration | Details |
|-------|--------|----------|---------|
| crawl | ✅ ok | 0.72s | {'sites': {'pontv-movies': {'status': 'failed', 'error': "[Errno 21] Is a directory: '/mnt/d/Downloa |
| generate | ✅ ok | 10.48s | {'feeds_generated': 1, 'feeds_failed': 0, 'exit_code': 0} |

## Feed Artifacts

### generate (1 feeds)

- `pontv-movies.xml`

## Feed Quality (post-fix)

| Feed | Items | Status |
|------|-------|--------|

## Artifact Hashes

```json
{
  "00_config/sites.yaml": "04278285c96caf126f206309ca2bad0ab4526b9eec41b30f61de1b3bf37255fd",
  "01_generate/pontv-movies.xml": "96403c7a394f80e3dbefa72d4485cc70988ae702ab609a2c795e5d7246371e86",
  "cache/cache.json": "0c67e8faee812751ce4f6003e438f67e6bbc0f7a14b19e034208d9a4a320bb5c",
  "feeds_working/pontv-movies.xml": "96403c7a394f80e3dbefa72d4485cc70988ae702ab609a2c795e5d7246371e86",
  "logs/generate.log": "f870a9d6b4c6ad42262ed3bd8211f9cfef5c1a9608f6fb97e402539e3ccb9e39",
  "01_crawl/recordings/index.json": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
}
```