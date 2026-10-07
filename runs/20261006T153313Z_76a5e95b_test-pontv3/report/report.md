# Dev Harness Run Report: 20261006T153313Z_76a5e95b_test-pontv3

**Timestamp:** 20261006T153313Z
**Git SHA:** 76a5e95be0ac427101303e92e3feca15c36fe389 (dirty)
**Command:** `/mnt/d/Download/tools/rss-generator/dev_harness/__main__.py run --new pontv-movies --stages crawl,generate --label test-pontv3`
**TMDb Enabled:** True
**Replay Mode:** False

## Sites Selected

- pontv-movies 🆕

## Stage Results

| Stage | Status | Duration | Details |
|-------|--------|----------|---------|
| crawl | ✅ ok | 9.63s | {'sites': {'pontv-movies': {'status': 'ok', 'items': 'unknown'}}} |
| generate | ✅ ok | 8.75s | {'feeds_generated': 1, 'feeds_failed': 0, 'exit_code': 0} |

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
  "01_generate/pontv-movies.xml": "2a1ead1e56f334d36497aa95eee2583cc2c120a880c798869fffa4db8ff1a014",
  "cache/cache.json": "0c67e8faee812751ce4f6003e438f67e6bbc0f7a14b19e034208d9a4a320bb5c",
  "feeds_working/pontv-movies.xml": "2a1ead1e56f334d36497aa95eee2583cc2c120a880c798869fffa4db8ff1a014",
  "logs/generate.log": "be704fbe3995cbc98e073a1d18251b389ccbc684c251716136c3714e23a7be01",
  "01_crawl/recordings/index.json": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
}
```