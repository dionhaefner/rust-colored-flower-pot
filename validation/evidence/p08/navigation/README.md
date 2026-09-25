# Map navigation: singleband and RGB

Primary metric: time from requesting newly visible tiles until the last response arrives for a 1024×768 map view. A session retains tiles already downloaded; horizontal pans request three new tiles, vertical pans four. Each session has up to six in-flight requests. First-view timing covers twelve tiles from a fresh service, after `/keys` readiness. This measures server tile delivery, not browser animation, compositing or network latency.

Three repetitions per engine/mode/session count, alternating engine order. Table values are medians of per-trial statistics, rounded to milliseconds; raw samples and trial ranges remain in JSON. A single session has six measured pans per trial, so its tail estimate is based on few view events. More users have spatially offset tracks with natural overlap; this is one specified browsing workload, not a universal traffic distribution.

| Users | Mode | Python pan p50 / p95 ms | Rust pan p50 / p95 ms | Pan speedup | Python / Rust first-view p50 ms |
| --- | --- | --- | --- | --- | --- |
| 1 | singleband | 62 / 91 | 54 / 80 | 1.15× | 305 / 186 |
| 1 | rgb | 165 / 243 | 129 / 192 | 1.28× | 630 / 468 |
| 4 | singleband | 97 / 168 | 58 / 100 | 1.67× | 704 / 521 |
| 4 | rgb | 459 / 664 | 323 / 481 | 1.42× | 1813 / 1507 |
| 8 | singleband | 314 / 488 | 163 / 309 | 1.92× | 1389 / 932 |
| 8 | rgb | 1096 / 1585 | 731 / 935 | 1.50× | 3846 / 2979 |

## Conditions and validity

- Full, unmodified Sentinel-2 RGB COGs with original overviews and pinned source hashes; zoom 13. Every measured image is checked against frozen Python. All measured tiles are at least 95% opaque, avoiding empty-tile speed inflation.
- Three affinity-constrained CPU cores for each engine. Three Gunicorn sync processes versus three native raster workers. GDAL cache is 32 MiB per Python process versus 96 MiB shared native: equal 96 MiB aggregate budgets. Application caches total 64 MiB each, compressed/per-process in Python and uncompressed/shared in Rust.
- Local raster access and warm OS disk cache. No WAN, real S3, cold-disk, browser paint or cancelled-request claim. The client pauses 100 ms after each completed view, then pans; this does not measure continuous-drag animation.
- Native queue capacity is configured to 256 for concurrent tile bursts. This is a capacity setting, not a changed default; no failed requests are retried or removed. CPU time and RSS peaks are retained; RSS sums shared Python pages and is not PSS.
- Initial bounding-box tracks hit substantial nodata and were rejected as unrepresentative. Their report remains in `validation/artifacts/p08-navigation`. The first valid-track attempt briefly overlapped that pilot and was stopped; it remains in `p08-navigation-valid`. Only the isolated `p08-navigation-final` run supplies these figures.

Observed 412 distinct tile URLs, minimum opaque coverage 100.0%; 4992 requests and 0 HTTP failures. Raw commands, paths, image hashes, timings, CPU/RSS and native queue/cache metrics are in [report.json](report.json).

## Reproduce

Run alone on the host, with the frozen environment, full cached corpus and final native candidate available:

```sh
PYTHONPATH=.cache/reference .venv-reference/bin/python validation/tools/p08_navigation.py --out validation/artifacts/NEW-navigation
.venv-reference/bin/python validation/tools/p08_navigation_report.py --input validation/artifacts/NEW-navigation/report.json --out validation/evidence/NEW-navigation
```

This follow-up does not modify the native implementation or replace/reseal the earlier P08 archives. It answers map-navigation serving performance with corrected aggregate cache budgets; older repeated-tile/expression microbenchmarks remain separate evidence.
