# Dev Harness Run Report: 20261006T152732Z_76a5e95b_test-pontv

**Timestamp:** 20261006T152732Z
**Git SHA:** 76a5e95be0ac427101303e92e3feca15c36fe389 (dirty)
**Command:** `/mnt/d/Download/tools/rss-generator/dev_harness/__main__.py run --new pontv-movies --stages crawl,generate --label test-pontv`
**TMDb Enabled:** True
**Replay Mode:** False

## Sites Selected

- bingebang-movies.xml 📊
- hydrahd-movies.xml 📊
- pontv-movies.xml
- ridomovies-movies.xml 📊

## Stage Results

| Stage | Status | Duration | Details |
|-------|--------|----------|---------|
| crawl | ✅ ok | 0.31s | {'sites': {'bingebang-movies.xml': {'error': 'Site not found'}, 'hydrahd-movies.xml': {'error': 'Sit |
| generate | ✅ ok | 16.46s | {'feeds_generated': 4, 'feeds_failed': 0, 'exit_code': 0} |

## Feed Artifacts

### generate (4 feeds)

- `bingebang-movies.xml`
- `hydrahd-movies.xml`
- `pontv-movies.xml`
- `ridomovies-movies.xml`

## Feed Quality (post-fix)

| Feed | Items | Status |
|------|-------|--------|

## Artifact Hashes

```json
{
  "00_config/sites.yaml": "04278285c96caf126f206309ca2bad0ab4526b9eec41b30f61de1b3bf37255fd",
  "01_generate/bingebang-movies.xml": "7b3fa6afce8df0955da4f17a8178e6c2017c99ded35639fd5cd1b0f8cf5616f2",
  "01_generate/hydrahd-movies.xml": "287608511e9290ece092b4ec95468572e32aba3fe2fc4d8daf591f1f3aa936be",
  "01_generate/pontv-movies.xml": "1149c4d1adfc0a4476dce9a0791596eaecdd6cf86f1b343e92b38c82bc890bfb",
  "01_generate/ridomovies-movies.xml": "6798397b2aa9e92e9cc0ab93dad08b66ac9c76161557b85a794e0f7f4e514893",
  "cache/cache.json": "8ff3e31c7f2a656043021f87ec61c6cb8dc6a7fa82ff84bfa73fb3e35233e85e",
  "feeds_working/bingebang-movies.xml": "7b3fa6afce8df0955da4f17a8178e6c2017c99ded35639fd5cd1b0f8cf5616f2",
  "feeds_working/hydrahd-movies.xml": "287608511e9290ece092b4ec95468572e32aba3fe2fc4d8daf591f1f3aa936be",
  "feeds_working/pontv-movies.xml": "1149c4d1adfc0a4476dce9a0791596eaecdd6cf86f1b343e92b38c82bc890bfb",
  "feeds_working/ridomovies-movies.xml": "6798397b2aa9e92e9cc0ab93dad08b66ac9c76161557b85a794e0f7f4e514893",
  "logs/generate.log": "cee16aa9cb61f0c57d7d128a087c40ae1a91acd5a003407b25e4b070bae956c2",
  "01_crawl/recordings/index.json": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
}
```