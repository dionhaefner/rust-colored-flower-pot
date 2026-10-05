# Performance and fair comparisons

Measure what your users wait for: a complete first viewport, newly exposed tiles
while panning, and tail latency at expected concurrency. Repeated requests to
one warm URL are a useful cache probe but do not represent all map navigation.
Response receipt, image decode, browser paint, WAN latency, and source-service
access are separate stages.

A fair Terracotta comparison uses the same immutable rasters, catalog contents,
GDAL behavior, requests, CPU allocation, and total cache budgets. Account for
all Python worker caches and native decoded-source/GDAL caches, not just one
application setting. Compare multiple fresh-process paired trials, report p50
and p95 plus failures, and verify decoded imagery. Do not sum process RSS and
call it private memory savings: shared pages can be counted multiple times.

## Reproduce the local example workload

On Linux with CPython 3.12, create a separate environment for Terracotta and
its Gunicorn server. These versions match the comparison below:

```sh
python3.12 -m venv benchmark-terracotta
benchmark-terracotta/bin/python -m pip install terracotta==0.10.1 rasterio==1.4.4 gunicorn==23.0.0
```

From the RCFP environment, use the same catalog/raster paths and run the included
benchmark with a fresh output directory:

```sh
python docs/tools/benchmark.py --reference-python benchmark-terracotta/bin/python --out benchmark-results
```

The tool runs native serving and three Terracotta Gunicorn workers with matched
GDAL/application budgets, one- and two-client preview requests, three paired
trials, and exact decoded-image checks. This small local warm-preview workload
uses supplied raster samples. It does not establish pan-track speed, cold-disk
behavior, remote throughput, browser paint, cloud cold starts, or production
memory use. The output identifies package/runtime versions, native binary and
raster hashes, parameters, failure counts, and latency distributions.

## Local measured example

A 2026-10-05 Linux x86_64 run used the development wheel
`0.1.1.dev9+g9de64ebdd.d20261005`, GDAL 3.10.3 in both services,
three pinned CPU cores, three native raster workers, and three Gunicorn 23.0.0
workers. Both services had 96 MiB aggregate logical cache budgets: native
48 MiB GDAL plus 48 MiB arrays; Python three times 16 MiB GDAL plus 16 MiB
compressed application cache. Equal logical budgets do not prove equal physical
memory use. Fast paths, decoded-source cache, and prefetch were disabled.

The table reports medians of three per-trial p50 and p95 values, using 30 timed
requests per cell after nine warmup requests. All 720 timed requests succeeded;
all decoded images matched exactly. Singleband and RGB used the supplied
512×512 UTM source windows and produced 256×256 previews over loopback HTTP.

| Preview    | Clients | RCFP p50 / p95 (ms) | Terracotta p50 / p95 (ms) | p50 ratio |
| ---------- | ------: | ------------------: | ------------------------: | --------: |
| singleband |       1 |         1.03 / 1.25 |               6.10 / 7.32 |     5.92× |
| singleband |       2 |         0.72 / 1.41 |               6.35 / 7.89 |     8.82× |
| rgb        |       1 |         2.44 / 2.72 |             17.16 / 20.44 |     7.04× |
| rgb        |       2 |         2.45 / 2.97 |             16.07 / 20.81 |     6.55× |

These measurements show faster warm local preview delivery for this artifact
and workload. They do not measure navigation, cold disk, WAN, browser paint,
cloud starts, or private memory savings. The native socket adapter admits two
simultaneous renders; overload studies must include rejected requests instead
of silently retrying them. Download the [full result and methodology](../_static/benchmark.json),
including per-request timings, input hashes, CPU affinity, and the measured
native executable hash. This is an example comparison, not a release-wide
speed guarantee.

## Opt-in paths

`TC_RUST_MERCATOR_FAST=1` enables compatible affine Web Mercator reads where the
input satisfies the fast path's conditions. Other inputs fall back to GDAL.
`TC_PREFETCH_TILE_RING=1` uses idle workers to prepare neighbors within bounded
caches; measure its foreground effects and budget. `TC_SOURCE_BLOCK_CACHE_BYTES`
sets a separate decoded-source block cache, disabled by default.

`TC_RUST_UTM_FAST=1` is native-only and can change sampling values. Compatibility
mode disables it. Interior high-detail pans, overview-scale/edge cases, first
views, and loaded RGB p95 are different workloads. A speedup for one is not a
claim about the others. Enable flags only after your own image and load checks;
see [resource tuning](../how-to/resources.md).

## Native map navigation

Native mode now uses a general direct output-grid tile reader through GDAL/PROJ,
with fused stretch/packing when no color transform is requested. It applies
across source CRSs and scalar dtypes; it is not restricted to one UTM zone.
`TC_NATIVE_DIRECT_READ=false` restores the intermediate reader. The Terracotta
profile disables direct-grid sampling. Native samples can differ from that
intermediate grid; geographic coverage/categories and image quality are checked
independently. Previews retain the general reader.

A local Linux/GDAL 3.10.3 navigation experiment used checksum-pinned complete
Sentinel-2, WorldCover and northern/southern DEM scenes plus real multiband Byte
imagery, SQLite, four pinned cores/workers, fixed 3×3 viewports at three zooms,
and one/three overlapping or independent users. Three sequential paired trials
compared RCFP before/after the changes with equal 128 MiB logical cache budgets.
The candidate divided 64 MiB of application cache into 48 MiB arrays and 16 MiB
finished images and enabled 60-second remote source/image retention for the
stable fixtures. GDAL had another 64 MiB on both sides.

Across workload-group medians, local first viewports improved 1.7–2.7× and pans
1.0–1.7×. With a controlled 15 ms source range-server latency, first views
improved 1.2–1.5× and pans 1.6–3.1×. All 216 case runs succeeded. These are
existing-asset comparisons against earlier RCFP, not Terracotta, physically cold
disk, browser paint or production S3. Loaded tails and cache reuse differ by cell;
ratios must not be multiplied with individual optimization gains.

Actual public S3 elevation trials completed 3402 requests without failures.
Pans improved 2.1–3.8× across the concurrency cells; first views were nearly
neutral and loaded p95 was mixed. The broader S3 imagery control recorded three
RGB deadline failures and is excluded from speed ratios. Keep failed requests in
your own reports rather than retrying them out of the results.

Map-grid derivatives with larger blocks improved many local/remote workloads,
but small blocks greatly increased range round trips and some large-block zooms
still regressed. Bundled RGB can help RGB remote access while penalizing
singleband views. Native RGB WebP at quality 85 reduced response bytes about 13×
in the experiment, with exact coverage and more encode CPU/slower loopback
completion. These remain workload choices, not automatic defaults. PNG stays the
default. See [map preparation](../how-to/optimize.md),
[HTTP formats/bands](../reference/http.md), and
[cache/resource controls](../how-to/resources.md).

## Terracotta and TiTiler navigation comparison

A 2026-10-05 Linux x86_64 run on an AMD Ryzen 5 5600 measured the native
development executable with SHA-256
`4624c1e46b84613b6d496ed9a928685bab62d3a696631f64e89898dbec8f7ed3`.
All 216 fresh-service cases and 45,360 tile requests succeeded. Each service
completed the same 15,120 requests. The table reports medians across nine
singleband cells (three raster families × three user arrangements) or three RGB
cells. Each cell first takes the median of its three trials; pan/zoom timings
also take the median of that case's navigation steps. Ratios summarize the
cell-by-cell ratios, so they need not equal a ratio of the aggregate time columns.

| Storage | Layer | Action | RCFP (ms) | Terracotta (ms) | TiTiler (ms) | vs Terracotta | vs TiTiler |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Local | Singleband | First viewport | 70.9 | 331.6 | 209.4 | 4.17× | 3.58× |
| Local | Singleband | Pan | 5.1 | 87.3 | 46.7 | 9.52× | 8.69× |
| Local | Singleband | Zoom | 27.8 | 223.5 | 124.2 | 7.09× | 4.34× |
| Local | RGB | First viewport | 174.1 | 627.9 | 737.6 | 3.61× | 3.88× |
| Local | RGB | Pan | 19.3 | 213.4 | 234.9 | 5.77× | 4.64× |
| Local | RGB | Zoom | 130.1 | 615.1 | 687.5 | 3.66× | 4.05× |
| Remote ranges | Singleband | First viewport | 226.6 | 541.1 | 313.9 | 2.01× | 1.45× |
| Remote ranges | Singleband | Pan | 34.8 | 93.6 | 55.0 | 2.43× | 1.89× |
| Remote ranges | Singleband | Zoom | 88.0 | 280.2 | 148.3 | 2.41× | 2.22× |
| Remote ranges | RGB | First viewport | 520.4 | 1277.3 | 849.3 | 2.38× | 1.58× |
| Remote ranges | RGB | Pan | 87.9 | 307.2 | 224.4 | 4.00× | 2.56× |
| Remote ranges | RGB | Zoom | 286.7 | 940.3 | 711.4 | 2.93× | 2.24× |

The loaded request distribution below combines local/remote, all layers and
navigation/style/expression stages for both three-user arrangements, with
12,960 requests per service. Memory is the median of the sampled peak across
all cases, not a process memory limit or a sustained-capacity claim.

| Service | Loaded p50 / p95 / p99 (ms) | Median sampled peak PSS / USS (MiB) |
| --- | ---: | ---: |
| RCFP | 4.56 / 120.38 / 219.76 | 195 / 194 |
| Terracotta | 78.18 / 336.14 / 500.38 | 300 / 236 |
| TiTiler/rio-tiler | 83.32 / 348.15 / 539.56 | 544 / 480 |

Across the remote cases, source GETs / fetched MiB totaled 3,383 / 2,464 for
RCFP, 7,420 / 5,219 for Terracotta and 11,021 / 9,075 for TiTiler. Median
service startup-to-readiness was 51 / 460 / 1,160 ms respectively and is reported
separately from the first image. TiTiler readiness requires all four worker PIDs.
Startup prepares no raster. Timed-batch throughput, excluding startup and client
decode, was 279 / 69 / 72 requests/s for this request mix; this is not maximum
sustained capacity. Of each service's
15,120 requests, 1,260 were transparent and 12,210 entirely valid; the rest
intersected source/nodata edges. Local and remote decoded images matched exactly
within every service/trial/user cell.

Independent saved-image checks covered 252 distinct scene/style/tile cases per
service. RCFP matched the direct GDAL output-grid samples and masks exactly,
including all 54 category tiles. Terracotta differed by 1,595 coverage pixels
across those checks; TiTiler's masks matched exactly. For continuous images,
mean per-tile channel differences were 0.18 and 0.46 display levels for
Terracotta/TiTiler, with worst individual differences 70 and 6. Category boundary
samples differed too: 24/54 Terracotta and 42/54 TiTiler category tiles matched
exactly; category labels differed at 2.51% and 0.91% of compared valid pixels,
respectively, while all colors remained in the requested palette. These differences
are recorded rather than counted as exact-fidelity passes; the named Terracotta
profile is not the RCFP profile timed here. Adjacent output grids were checked
against their independent affine bounds; this does not qualify every seam or
mosaic workflow.

The maintained `tests/service_benchmark.py` runner compares native RCFP,
Terracotta 0.10.1 and TiTiler 2.4.0 / rio-tiler 9.4.9 on complete real
Sentinel-2 (10980² UInt16), Copernicus DEM (3600² Float32) and WorldCover
(36000² UInt8) sources. Separate red/green/blue Sentinel COGs provide the RGB
layer. Acquisition URLs, attribution and full SHA-256 hashes are pinned in
`tests/fixtures/navigation.json`. Sources are not reprojected or repacked for
these comparisons. SQLite catalogs contain the same rows and precomputed
metadata; numeric stretches avoid differences in metadata percentile estimates.

Each case starts a fresh service. Three trials rotate service order across
RCFP, Terracotta and TiTiler. A 3×3 viewport visits three zooms, pans by one tile
at a time, revisits the original view, then changes the stretch. Sentinel
singleband also evaluates a NIR/red ratio expression. One user and three users
with overlapping or independently displaced views issue four requests per user.
HTTP response completion precedes a separately timed client decode/record stage;
there is no browser or client WAN in the measured path. Failed requests remain
in the report and are not retried.

All services use the same four physical serving cores and GDAL 3.10.3. Remaining
physical cores run clients and the source origin. Terracotta uses four Gunicorn
23.0.0 sync workers with its internal multiprocessing disabled; TiTiler uses four
Uvicorn 0.54.0 workers with the default FastAPI threadpool and normal parallel
rio-tiler asset reads; RCFP uses four raster workers. TiTiler timing starts only
after health responses confirm that all four worker PIDs are ready. Each service has a 160 MiB
aggregate logical cache budget, including curl's downloaded-range cache:

| Service | GDAL blocks | Raster arrays | Downloaded ranges |
| --- | ---: | ---: | ---: |
| RCFP | 64 MiB | 64 MiB | 32 MiB |
| Terracotta | 4 × 16 MiB | 4 × 16 MiB | 4 × 8 MiB |
| TiTiler/rio-tiler | 4 × 32 MiB | none | 4 × 8 MiB |

Finished-image caches, prefetch and RCFP remote dataset leases are off. Cache
equality does not imply equal physical memory. Process-tree memory samples every
100 ms sum PSS (which apportions shared pages) and USS (private resident pages),
including Python masters/workers; summed RSS is diagnostic only. Reports retain
request p50/p95/p99, throughput, CPU, memory and source GET/HEAD/range bytes.

The TiTiler test application resolves the shared SQLite catalog into STAC
assets and uses the normal multi-asset factory/reader. It selects 256-pixel PNGs,
compression level 1 and the same source bands, stretches and category palette.
Its renderer uses rio-tiler's own stretch with output range 1–255 to match
Terracotta's reserved masked zero. Out-of-source tiles become transparent PNGs.
This adapter adds catalog resolution; there is no remote STAC discovery or
custom raster decoder. Continuous tiles request bilinear warp/read and category
tiles nearest. Intermediate grids, overview selection and integer rounding
still differ. These are comparable display workloads, not bit-identical output
contracts. `service_benchmark_quality.py` checks saved tiles against independent
GDAL output-grid reads and reports coverage/value/category differences separately.
The STAC adapter's required timestamp is a placeholder and is not a temporal
selection or acquisition-date claim.

Reproduce on Linux with separate CPython 3.12 environments installed from
`tests/benchmarks/terracotta.lock` and `tests/benchmarks/titiler.lock`, and a
configured native GDAL runtime:

```sh
python tests/navigation_sources.py
python tests/service_benchmark.py --binary target/release/rcfp --reference-python /path/to/terracotta-env/bin/python --titiler-python /path/to/titiler-env/bin/python --out build/service-comparison --trials 3
python tests/service_benchmark_quality.py --results build/service-comparison --out build/service-quality
```

Generated reports and captured tiles belong in ignored `build/`. Reports record
executable, manifest and adapter hashes plus package/native versions; installed
code fingerprints can be captured with `tests/service_benchmark_environment.py`
in each Python environment. Logical cache controls, CPU allocation and individual
request outcomes accompany each case.

Local sources have a warm OS page cache and fresh service/GDAL state. Remote
sources use a self-hosted byte-range origin with 15 ms delay per source request,
giving exact request/byte accounting; this is a controlled remote surrogate,
not an actual S3 comparison. Physically cold disk, browser paint/WAN, private
credentials, true time-series acquisitions, packed multiband TIFF comparisons,
dynamic mosaics and cross-service deadline/disconnect draining remain separate.
The native resource suite establishes its own bounded-drain evidence. No public
tile service is load-tested and this benchmark does not qualify a portable wheel.
