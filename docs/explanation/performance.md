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
call it private memory savings because shared pages can be counted multiple times.

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
48 MiB GDAL plus 48 MiB arrays. Python three times 16 MiB GDAL plus 16 MiB
compressed application cache. Equal logical budgets do not prove equal physical
memory use. Fast paths, decoded-source cache, and prefetch were disabled.

The table reports medians of three per-trial p50 and p95 values, using 30 timed
requests per cell after nine warmup requests. All 720 timed requests succeeded.
All decoded images matched exactly. Singleband and RGB used the supplied
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
simultaneous renders. Overload studies must include rejected requests instead
of silently retrying them. Download the [full result and methodology](../_static/benchmark.json),
including per-request timings, input hashes, CPU affinity, and the measured
native executable hash. This is an example comparison, not a release-wide
speed guarantee.

## Opt-in paths

`TC_RUST_MERCATOR_FAST=1` enables compatible affine Web Mercator reads where the
input satisfies the fast path's conditions. Other inputs fall back to GDAL.
`TC_PREFETCH_TILE_RING=1` uses idle workers to prepare neighbors within bounded
caches. Measure its foreground effects and budget. `TC_SOURCE_BLOCK_CACHE_BYTES`
sets a separate decoded-source block cache, disabled by default.

`TC_RUST_UTM_FAST=1` is native-only and can change sampling values. Compatibility
mode disables it. Interior high-detail pans, overview-scale/edge cases, first
views, and loaded RGB p95 are different workloads. A speedup for one is not a
claim about the others. Enable flags only after your own image and load checks.
See [resource tuning](../how-to/resources.md).

## Native map navigation

Native mode uses a general direct output-grid tile reader through GDAL/PROJ,
with fused stretch/packing when no color transform is requested. It applies
across source CRSs and scalar dtypes.
`TC_NATIVE_DIRECT_READ=false` restores the intermediate reader. The Terracotta
profile disables direct-grid sampling. Native samples can differ from that
intermediate grid. Geographic coverage/categories and image quality are checked
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
disk, browser paint or production S3. Loaded tails and cache reuse differ by cell.
Ratios must not be multiplied with individual optimization gains.

Actual public S3 elevation trials completed 3402 requests without failures.
Pans improved 2.1–3.8× across the concurrency cells. First views were nearly
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

A 2026-10-06 Linux x86_64 run on an AMD Ryzen 5 5600 measured the native
development executable with SHA-256
`ecfdc05bbe70aa46354ee996d97c71dfb2a1a8d07845330b2294f023a29a1c8c`.
All 216 fresh-service cases and 45,360 tile requests succeeded. Each service
completed the same 15,120 requests. The table reports medians across nine
singleband cells (three raster families × three user arrangements) or three RGB
cells. Each cell first takes the median of its three trials. Pan/zoom timings
also take the median of that case's navigation steps. Ratios summarize the
cell-by-cell ratios, so they need not equal a ratio of the aggregate time columns.

| Storage       | Layer      | Action         | RCFP (ms) | Terracotta (ms) | TiTiler (ms) | vs Terracotta | vs TiTiler |
| ------------- | ---------- | -------------- | --------: | --------------: | -----------: | ------------: | ---------: |
| Local         | Singleband | First viewport |      64.6 |           336.8 |        225.6 |         5.21× |      4.75× |
| Local         | Singleband | Pan            |       4.3 |            84.2 |         50.9 |        18.83× |     13.27× |
| Local         | Singleband | Zoom           |      24.0 |           226.5 |        131.8 |         8.64× |      6.08× |
| Local         | RGB        | First viewport |     112.8 |           704.8 |        764.9 |         5.55× |      6.02× |
| Local         | RGB        | Pan            |      12.5 |           228.8 |        217.6 |         8.65× |      7.34× |
| Local         | RGB        | Zoom           |      73.8 |           533.2 |        687.3 |         3.92× |      4.33× |
| Remote ranges | Singleband | First viewport |     182.8 |           556.2 |        308.1 |         2.57× |      1.69× |
| Remote ranges | Singleband | Pan            |      10.5 |            85.7 |         57.6 |         9.21× |      8.46× |
| Remote ranges | Singleband | Zoom           |      35.2 |           270.5 |        153.1 |         4.34× |      4.35× |
| Remote ranges | RGB        | First viewport |     352.9 |          1333.1 |        881.9 |         3.30× |      2.15× |
| Remote ranges | RGB        | Pan            |      23.1 |           296.2 |        237.6 |        10.02× |      5.18× |
| Remote ranges | RGB        | Zoom           |     103.6 |           921.8 |        754.0 |         4.44× |      3.41× |

The loaded request distribution below combines local/remote, all layers and
navigation/style/expression stages for both three-user arrangements, with
12,960 requests per service. Memory is the median of the sampled peak across
all cases, not a process memory limit or a sustained-capacity claim.

| Service           | Loaded p50 / p95 / p99 (ms) | Median sampled peak PSS / USS (MiB) |
| ----------------- | --------------------------: | ----------------------------------: |
| RCFP              |       3.17 / 76.77 / 163.86 |                           214 / 213 |
| Terracotta        |     76.94 / 340.76 / 518.20 |                           299 / 235 |
| TiTiler/rio-tiler |     84.01 / 351.03 / 546.55 |                           546 / 491 |

Across the remote cases, source GETs / fetched MiB totaled 1,856 / 1,164 for
RCFP, 7,462 / 5,275 for Terracotta and 10,890 / 8,942 for TiTiler. Median
service startup-to-readiness was 51 / 459 / 1,155 ms respectively and is reported
separately from the first image. TiTiler readiness requires all four worker PIDs.
Startup prepares no raster. Timed-batch throughput, excluding startup and client
decode, was 436 / 69 / 72 requests/s for this request mix. This is not maximum
sustained capacity. Of each service's
15,120 requests, 1,260 were transparent and 12,210 entirely valid. The rest
intersected source/nodata edges. Local and remote decoded images matched exactly
within every service/trial/user cell.

Independent saved-image checks covered 252 distinct scene/style/tile cases per
service. RCFP matched the direct GDAL output-grid samples and masks exactly,
including all 54 category tiles. Terracotta differed by 1,595 coverage pixels
across those checks. TiTiler's masks matched exactly. For continuous images,
mean per-tile channel differences were 0.18 and 0.46 display levels for
Terracotta/TiTiler, with worst individual differences 70 and 6. Category boundary
samples differed too: 24/54 Terracotta and 42/54 TiTiler category tiles matched
exactly. Category labels differed at 2.51% and 0.91% of compared valid pixels,
respectively, while all colors remained in the requested palette. These differences
are recorded rather than counted as exact-fidelity passes. The named Terracotta
profile is not the RCFP profile timed here. Adjacent output grids were checked
against their independent affine bounds. This does not qualify every seam or
mosaic workflow.

The maintained `tests/service_benchmark.py` runner compares native RCFP,
Terracotta 0.10.1 and TiTiler 2.4.0 / rio-tiler 9.4.9 on complete real
Sentinel-2 (10980² UInt16), Copernicus DEM (3600² Float32) and WorldCover
(36000² UInt8) sources. Separate red/green/blue Sentinel COGs provide the RGB
layer. Acquisition URLs, attribution and full SHA-256 hashes are pinned in
`tests/fixtures/navigation.json`. Sources are not reprojected or repacked for
these comparisons. SQLite catalogs contain the same rows and precomputed
metadata. Numeric stretches avoid differences in metadata percentile estimates.

Each case starts a fresh service. Three trials rotate service order across
RCFP, Terracotta and TiTiler. A 3×3 viewport visits three zooms, pans by one tile
at a time, revisits the original view, then changes the stretch. Sentinel
singleband also evaluates a NIR/red ratio expression. One user and three users
with overlapping or independently displaced views issue four requests per user.
HTTP response completion precedes a separately timed client decode/record stage.
There is no browser or client WAN in the measured path. Failed requests remain
in the report and are not retried.

All services use the same four physical serving cores and GDAL 3.10.3. Remaining
physical cores run clients and the source origin. Terracotta uses four Gunicorn
23.0.0 sync workers with its internal multiprocessing disabled. TiTiler uses four
Uvicorn 0.54.0 workers with the default FastAPI threadpool and normal parallel
rio-tiler asset reads. RCFP uses four raster workers. TiTiler timing starts only
after health responses confirm that all four worker PIDs are ready. Each service has a 160 MiB
aggregate logical cache budget, including curl's downloaded-range cache:

| Service           | GDAL blocks | Raster arrays | Downloaded ranges |
| ----------------- | ----------: | ------------: | ----------------: |
| RCFP              |      64 MiB |        64 MiB |            32 MiB |
| Terracotta        |  4 × 16 MiB |    4 × 16 MiB |         4 × 8 MiB |
| TiTiler/rio-tiler |  4 × 32 MiB |          none |         4 × 8 MiB |

Finished-image caches and prefetch are off. Native RCFP uses its default
60-second remote source leases and shares identical active renders even with
zero finished-image retention. Source/overview handles add memory outside the
logical block/array/range budgets. The memory table includes that cost. Cache
equality does not imply equal physical memory. Process-tree memory samples every
100 ms sum PSS (which apportions shared pages) and USS (private resident pages),
including Python masters/workers. Summed RSS is diagnostic only. Reports retain
request p50/p95/p99, throughput, CPU, memory and source GET/HEAD/range bytes.

The TiTiler test application resolves the shared SQLite catalog into STAC
assets and uses the normal multi-asset factory/reader. It selects 256-pixel PNGs,
compression level 1 and the same source bands, stretches and category palette.
Its renderer uses rio-tiler's own stretch with output range 1–255 to match
Terracotta's reserved masked zero. Out-of-source tiles become transparent PNGs.
This adapter adds catalog resolution. There is no remote STAC discovery or
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
executable, manifest and adapter hashes plus package/native versions. Installed
code fingerprints can be captured with `tests/service_benchmark_environment.py`
in each Python environment. Logical cache controls, CPU allocation and individual
request outcomes accompany each case.

Local sources have a warm OS page cache and fresh service/GDAL state. Remote
sources use a self-hosted byte-range origin with 15 ms delay per source request,
giving exact request/byte accounting. This is a controlled remote surrogate,
not an actual S3 comparison. Physically cold disk, browser paint/WAN, private
credentials, true time-series acquisitions, packed multiband TIFF comparisons
and dynamic mosaics are explicit applicability limits. The deployed Lambda comparison below adds a client-network/private-S3 workload
with its own artifact identities and results. It does not extend these controlled
results to other deployments.
Deadline, disconnect, bounded rejection and recovery behavior have a separate
request-lifecycle correctness gate, without cross-service timing comparisons.
No public tile service is load-tested and this benchmark does not qualify a
portable wheel.

## Deployed Lambda comparison

On 2026-10-24, native RCFP and unmodified Terracotta 0.10.1 ran as separate
x86_64 Lambda container functions in `eu-north-1`, reading identical private S3
objects in that region. Both had 2 GiB memory, a 30-second timeout and one raster
worker per environment. During the browser test, both reserved-invocation
ceilings were 64; afterward they returned to eight. AWS chose environment reuse
and scaling. These are matching per-environment allocations, not fixed aggregate
CPU or RAM budgets across the whole service.

GDAL was 3.10.3 in both. Terracotta used Python 3.12.15, Rasterio 1.4.4 and
`serverless-wsgi` 3.1.0. RCFP used the native executable from commit `7659236`,
SHA-256 `1ba260c54163bcdd06a4613e3687767ffaf9e72f760c562cac847177af49f272`.
The catalog bytes and source objects were identical. The three complete 10980²
UInt16 Sentinel-2 band COGs pinned in `tests/fixtures/navigation.json` totaled
282,064,245 bytes. RGB read separate red, green and blue objects. Stretches were
0–3000 and output was 256-pixel PNG at compression level 1.

Both services had 64 MiB GDAL, 64 MiB application-raster and 32 MiB downloaded-range
logical budgets per environment. Finished-image caching was off. RCFP retained
its normal 60-second source leases; Terracotta retained its normal compressed-array
cache. Logical budgets do not imply equal physical memory or eviction behavior.

### Complete browser map actions

Playwright drove a real 1600×1000 Leaflet map in Chromium 140.0.7339.16.
Each session started at zoom 12, panned 512 pixels horizontally and 128 vertically,
zoomed to 13 then 14, panned at detail scale, zoomed out and revisited its initial
center. Leaflet retained its normal overlapping tiles and connection reuse.
Each action ended after current tiles loaded, fades and map animation finished,
visible images decoded and two animation-frame callbacks ran. This measures
complete-map readiness, not a compositor presentation timestamp. Local map assets
loaded before timing; no external basemap was requested.

The map issued **35 tile requests** for each first view and zoom, **10** for the
first pan and revisit, and **15** for the detail pan. Both backends received
identical paths and had identical visible tile grids. Chromium used six HTTP/1.1
connections, with excess requests queued by the browser. There was no fixed
four-request client limit. Overlapping map tiles were reused without requests;
all 2,100 issued image requests reached Lambda, with no HTTP-cache hits.

The table reports medians from three complete paired trials. Backend order
rotated for singleband and RGB. Each new map had a fresh browser context and
function generation before its first image, followed by normal navigation.
Ratios are medians of within-trial Terracotta/RCFP completion ratios, so they
need not equal the ratio of the displayed median times.

| Layer      | Map action           | RCFP (ms) | Terracotta (ms) | Paired ratio |
| ---------- | -------------------- | --------: | --------------: | -----------: |
| Singleband | First full view, z12 |     1,533 |           2,811 |        1.89× |
| Singleband | Pan, z12             |       672 |             795 |        1.17× |
| Singleband | Zoom to z13          |       854 |           1,316 |        1.54× |
| Singleband | Zoom to z14          |       613 |             966 |        1.58× |
| Singleband | Pan, z14             |       620 |             778 |        1.25× |
| Singleband | Zoom out to z12      |       908 |           1,274 |        1.39× |
| Singleband | Revisit              |       576 |             640 |        1.11× |
| RGB        | First full view, z12 |     2,188 |           4,238 |        1.91× |
| RGB        | Pan, z12             |       969 |           1,325 |        1.36× |
| RGB        | Zoom to z13          |     1,656 |           2,869 |        1.73× |
| RGB        | Zoom to z14          |       944 |           2,005 |        2.12× |
| RGB        | Pan, z14             |       791 |           1,222 |        1.43× |
| RGB        | Zoom out to z12      |     1,242 |           2,497 |        1.96× |
| RGB        | Revisit              |       656 |             907 |        1.40× |

All **2,100 tile requests and 84 map actions succeeded**, with no JavaScript
errors. Every request matched a CloudWatch REPORT. Each backend initialized six
environments for each of its six fresh maps, with **zero initializations during
pans, zooms or revisits**. CloudWatch's maximum environment memory observations
were 258 MiB for RCFP and 254 MiB for Terracotta. These high-water marks do not
establish private-memory savings. All three browser trials are retained; no
failed browser trial is excluded. A separate one-trial harness qualification
used a different view and omitted the fade wait, so its timings are excluded.

Decoded output was stable within each backend across all trials. Independent
GDAL output-grid checks covered **260 unique tile cases per backend**. RCFP
matched every value and mask exactly. Terracotta matched 18 tiles exactly and
had 576 mask-pixel disagreements across the checks. Mean valid-channel absolute
difference was 0.50 on a 0–255 display scale; the largest difference was 178.
Both live demo previews matched the separate maintained reference exactly.
Native and intermediate-grid sampling remain different rendering contracts.

### Scripted HTTP diagnostics

A separate nine-tile batch workload used one or four fresh HTTP connections at
a time, ending at response receipt before client decode. Its three complete
paired trials contained 2,214 successful requests and no post-warmup
initializations. Warm stage-median ratios were 1.20–1.71× at concurrency four
and 1.03–1.17× serially, with some individual serial regressions. These are batch
transport diagnostics rather than complete browser actions. The original fourth
trial had six client network errors and is excluded in full. A repeat was
interrupted after 639 requests with 249 client errors and is also excluded in
full. Both failed reports remain separate from the successful browser run.

The [deployment and measurement procedure](../how-to/lambda-comparison.md)
records image/catalog/source identities, individual outcomes, screenshots and
configuration restoration. The browser results support gains of about 1.9×
for first views, 1.17–1.43× for pans and 1.39–2.12× for zooms in this workload.
They do not establish a universal Lambda speedup. Other client networks, HTTP/2
frontends, sustained load, Lambda sizes/architectures and categorical rasters
need their own deployed measurements.

## Deployed EC2 comparison

On 2026-10-25, the same qualified native and Terracotta images, private S3
catalog and three full Sentinel COGs ran in turn on one `c7i-flex.large` in
`eu-north-1`. Each container had two logical CPU threads on one physical Intel
Xeon Platinum 8488C core, two workers, 2 GiB memory and the same 160 MiB aggregate
logical cache budget. Terracotta used two Gunicorn 23.0.0 sync workers with
internal multiprocessing disabled. Finished-image caching and prefetch were off.
RCFP retained its ordinary source leases and Terracotta its compressed-array
cache. A common nginx TLS proxy served six browser HTTP/1.1 connections.

Three paired trials drove the same complete Leaflet first-view/pan/zoom workload
as the Lambda test, including animation, fades, decoding and frame callbacks.
Backend order rotated. Each map started a fresh server after metadata readiness;
process startup was outside first-view timing. Lambda's first views included
initialization, so these tables are separate deployment comparisons. Flex CPU
capacity and this single client/time window limit extrapolation.

| Layer      | Map action           | RCFP (ms) | Terracotta (ms) | Paired ratio |
| ---------- | -------------------- | --------: | --------------: | -----------: |
| Singleband | First full view, z12 |     1,063 |           2,138 |        1.90× |
| Singleband | Pan, z12             |       678 |             998 |        1.59× |
| Singleband | Zoom to z13          |       765 |           1,836 |        2.40× |
| Singleband | Zoom to z14          |       495 |           1,427 |        2.68× |
| Singleband | Pan, z14             |       586 |             895 |        1.50× |
| Singleband | Zoom out to z12      |       764 |           1,287 |        1.68× |
| Singleband | Revisit              |       510 |             648 |        1.28× |
| RGB        | First full view, z12 |     1,925 |           4,638 |        2.40× |
| RGB        | Pan, z12             |       931 |           1,761 |        1.99× |
| RGB        | Zoom to z13          |     1,528 |           5,830 |        3.88× |
| RGB        | Zoom to z14          |       784 |           3,932 |        5.13× |
| RGB        | Pan, z14             |       710 |           1,888 |        2.66× |
| RGB        | Zoom out to z12      |     1,858 |           3,384 |        1.74× |
| RGB        | Revisit              |       561 |             895 |        1.53× |

Times are medians; ratios are medians of within-trial ratios. All **84 map
actions and 2,092 tile requests succeeded**, with no JavaScript errors. Every
request joined to its proxy record. Visible tile grids matched throughout.
Leaflet reused extra tiles during Terracotta's singleband zoom-out in two
trials, issuing 28 and 34 requests instead of RCFP's 35. Those valid navigation
runs are retained. All other paired action request sets matched. Results are
complete-map measurements rather than forced identical tile batches.

Independent GDAL checks again covered 260 unique tiles per backend. RCFP matched
all values and masks exactly; Terracotta had the same 576 mask disagreements
and 0.503 mean valid-channel absolute difference as on Lambda. The local-source
diagnostic produced identical PNG bytes to S3 for all 260 tiles within each
backend. Neither service changed its imagery to obtain the local-source timings.

### Where the time goes

The server improvements are larger than the complete-map improvements. In the
unprofiled navigation runs, median paired Terracotta/RCFP container CPU ratios
were **7.69× for singleband and 5.82× for RGB**. No cgroup quota throttling was
recorded; host steal time stayed below 0.04%. These observations do not establish
sustained Flex capacity or a language-only performance difference.

At singleband detail zoom, median proxy upstream response times were **6 ms for
RCFP and 155 ms for Terracotta**, while complete-map times were **495 and
1,427 ms**. At RGB detail zoom those upstream medians were **14 and 522 ms**,
with map times **784 and 3,932 ms**. Upstream times include application queueing,
I/O and encoding. They are not isolated raster CPU times or a map critical path.
Browser connection limits, queued requests and WAN transfer remain outside those
measurements. Both maps also spent about **250 ms after their last response**
finishing visual readiness. Pans waited about another 200 ms before dispatching
their first requests under the normal animation policy.

A separate three-trial run disabled animation. Singleband/RGB detail-zoom gains
were **4.06×/7.38×**, compared with **2.68×/5.13×** normally. Detail-zoom request
sets matched across backends and animation policies. Animation also changes
Leaflet retention on other actions, so the diagnostic is not a uniform fixed
amount to subtract. Some zoom-outs and revisits used retained tiles and sent no
requests. All 84 diagnostic actions and 1,583 issued requests succeeded.

Source access also matters. Three paired trials with the same COGs on the host's
warm filesystem completed 2,100 requests successfully. RGB first-view medians
fell from **1,925/4,638 ms** on S3 to **1,178/2,637 ms** locally for
RCFP/Terracotta. RGB detail zoom was **781/2,353 ms** locally, compared with
**784/3,932 ms** on S3. This intervention removes remote access and its credential
path together; it does not isolate S3 network latency. Exact source GET counts
were not collected.

Separate Linux CPU-clock sampling, without Python call tracing, collected
118/832 singleband and 415/2,462 RGB samples for RCFP/Terracotta, with none lost.
In RGB, about **41% of RCFP samples were in GDAL, 15% in libdeflate and 10% in
libtiff**. Bilinear warp, decompression and TIFF predictor work dominate those
libraries. Terracotta's RGB samples included **28% in Python, 16% in zlib,
13% in libdeflate and 10% in GDAL**. Its normal array cache compresses data and
masks at level 9 in this configuration. Both services also encode PNG responses.
Shared GDAL version numbers do not imply identical dependency builds or sampling
algorithms.

A separate instrumented call trace recorded 154 singleband and 468 RGB AWS
session constructions through Rasterio, with repeated boto3/botocore session and
credential setup. Rasterio's [credential wrapper](https://github.com/rasterio/rasterio/blob/1.4.4/rasterio/env.py)
constructs a session for an S3 path when its environment lacks credentials.
This identifies a concrete candidate for session reuse in a tuned Terracotta
host. The tracer adds substantial overhead, so its cumulative seconds are not
reported as normal-runtime percentages. Native RGB array-cache metrics recorded
zero hits across 525 band jobs; its gain cannot be explained by array-cache hits
alone. Source-handle and GDAL/range-cache reuse still apply.

These results support deployed map gains up to about **5.1× for this EC2
workload**, alongside smaller gains for some pans and revisits. Client visual
policy and transport explain part of the gap between server and map speedups.
RCFP's remaining sampled CPU is mainly in raster processing and decompression;
Terracotta tuning could reduce session and cache-compression costs. Neither
hypothesis has an optimization speedup assigned without another measurement.

The [EC2 procedure](../how-to/ec2-comparison.md) records reproduction, profiling
and cleanup. Raw trials, proxy joins, counters, profiles, screenshots, source
snapshots, generated plots and exclusions are retained in ignored
`build/ec2-comparison-20261025/`. The temporary host stack was removed after
collection. Shared demo storage and Lambda endpoints remain deployed. This does
not qualify sustained load, other scenes, HTTP/2, other host sizes or physically
cold storage.

### Shared-server map sessions

A separate 2026-10-25 study measured independent 1600×1000 Leaflet maps sharing
that server configuration and the same S3 COGs. RCFP used a 256-job raster queue.
Terracotta ran both its level-9 array-cache compression and the supported level-1
setting, with unchanged package images and logical cache budgets. A separate
EC2 browser client added 20 ms network delay in each direction at an aggregate
1000 Mbit/s. The Sentinel views covered valid imagery around 45.55°N, 12.65°E at
zoom 14. Initial map readiness preceded the measured schedule.

Each user issued 12 pans 2.2 seconds apart, two bursts of 12 pan/zoom inputs
300 ms apart, and six band/stretch changes 2.2 seconds apart. Inputs continued
while earlier tiles were unfinished. Three trials rotated backend order and
reversed user-count order in the middle trial. Spatially separate maps tested
one, four and eight active users. The primary metric was the fraction of targets
fully ready within 1.5 seconds, retaining unfinished targets in the denominator.
Readiness includes decode, full tile opacity, the intended center/zoom and two
coverage samples. It does not measure compositor presentation.

With **four independent users**, the measured fractions were:

| Layer      | Target                   |  RCFP | Terracotta, level 9 | Terracotta, level 1 |
| ---------- | ------------------------ | ----: | ------------------: | ------------------: |
| Singleband | Paced pan                |  100% |               95.8% |               97.9% |
| Singleband | Final view after a burst |  100% |               70.8% |               70.8% |
| Singleband | Band/stretch change      |  100% |               63.9% |               66.7% |
| RGB        | Paced pan                |  100% |                2.8% |               20.8% |
| RGB        | Final view after a burst |   50% |                  0% |                  0% |
| RGB        | Band/stretch change      | 84.7% |                  0% |                  0% |

Each cell includes 144 paced targets, 24 final burst targets or 72 style targets
across three trials. The first style change also recenters the map. All 24 RCFP
RGB burst targets completed within the 8.7-second recovery window, with an
observed p95 of **3.71 seconds**. Neither Terracotta setting completed a final
RGB burst target within that window at four users, so its p95 is censored.
For one RGB user, all six burst targets completed on both sides. Pooled median
recovery was **0.84 seconds for RCFP and 2.13 seconds for tuned Terracotta**, about
2.5× faster in this particular workload.

For paced singleband views, the largest tested load meeting at least 95% of
1.5-second deadlines without request failures was eight users for RCFP and four
for both Terracotta settings. These are tested load brackets, not exact maximum
capacities. Eight-user RGB exceeded RCFP's useful operating range here: only
78.8% of paced targets met 1.5 seconds, and rapid navigation caused **56 HTTP 503
responses** from the raster queue. Those failures remain in the results. Both
Terracotta settings completed fewer than 1% of paced RGB targets within 1.5
seconds at that load, without 503 responses.

All 54 client timing checks met the declared limits. Per-case p95 sampling gaps
were 67–117 ms and p95 input lateness stayed at or below 14 ms. Independent GDAL
checks covered **2,556 sampled RCFP scene/style/tile cases**, all exact and stable,
with no transparent-only samples. Terracotta's sampled masks matched; mean
valid-channel differences were about 0.017–0.018 display levels, with a maximum
individual difference of 178. The rendering contracts remain distinct.

Another 36 cases tested four overlapping Sentinel maps, elevation and categorical
land cover. Overlapping RGB views improved tuned Terracotta's paced-view result
to **61.8%**, compared with 20.8% for separate views; RCFP reached 100% in both.
For elevation, all 24 burst targets completed in both services. Observed recovery
p95 was **1.34 seconds for RCFP and 3.99 seconds for tuned Terracotta**, about 3×.
Both services' sampled elevation outputs matched the reference exactly.

On nearest-sampled WorldCover, RCFP and tuned Terracotta both reached **every
1.5-second target**. This workload therefore shows a smaller latency advantage.
Across all 90 cases, all client timing checks passed and **3,906 sampled RCFP
outputs matched the reference exactly**. Terracotta's sampled land-cover class
labels differed from direct-grid sampling at 4.1–4.2% of compared valid pixels,
with matching coverage masks. Those sampling differences are separate from the
latency measurements.

The [session procedure](../how-to/ec2-comparison.md#measure-multiple-users-and-interrupting-navigation)
records reproduction and failure accounting. Raw sessions, proxy joins, sampled
imagery, plots and exclusions are retained in ignored `build/map-users-20261025/`.
These observations apply to the recorded resource budgets, images, frontend and
arrival pattern. Three correlated trials do not establish production tail-latency
guarantees or a universal Terracotta speedup.
