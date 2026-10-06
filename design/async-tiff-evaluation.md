# Async-TIFF evaluation

Native mode includes async-tiff 0.4.0 for exact format access, with GDAL fallback.
The standalone raster probe evaluates broader transport, caching and sampling
approaches. Its maintained code lives in
`tests/benchmarks/async-tiff/` and `tests/async_tiff_benchmark.py`. Acquired sources,
derivatives, binaries, independent expectations, and reports belong in ignored
storage. GDAL/PROJ remain required runtime dependencies.

## Production boundary

`src/raster/tiff.rs` reads eligible local TIFFs and anonymous HTTP range sources.
Header access supplies dimensions, overview counts, band counts and embedded
nodata. Native tiles use TIFF pixels only when a contained, north-up EPSG:3857
request matches a stored base or overview grid. Nearest layer grids require
base pixels and finite or absent nodata because the layer coverage VRT has
different overview and nonfinite nodata behavior. Statistics retain GDAL because
the measured TIFF path regressed both local and range reads.
No experimental sampler or decoded-block cache is used in production.

Lossless tiled scalar reads cover uncompressed, Deflate, LZW and Zstandard data,
including BigTIFF, both byte orders, internal masks and alpha. Predicted
planar pixels use GDAL because async-tiff 0.4.0 applies chunky sample spacing to
their predictor. Stripped pixels, other codecs,
sidecars, private tags/metadata, arbitrary resampling/projection, authenticated
cloud paths and explicit GDAL options also use GDAL. Detailed asset inspection
retains GDAL's CRS, scale/offset, category and color interpretation.

Each worker owns a current-thread runtime and up to 12 TIFF headers. Headers
and cached remote compressed ranges share a 4 MiB logical budget. Remote
headers use the existing source lease TTL. Local size/mtime changes and layer
revisions invalidate reuse. Reads fetch at most
four blocks concurrently, bound each range to 8 MiB and each decoded block to
16 MiB, and bound directory counts, metadata allocation estimates and codec
output. Remote pixels require a strong ETag, consistent version responses and
`If-Match`. Incorrect ranges, unavailable versions and failed reads return to
GDAL before exposing arrays. These limits describe individual buffers, not RSS.

`TC_ASYNC_TIFF=false` disables the path. The named Terracotta profile disables it
regardless of the setting. `tests/tiff_backend.py` qualifies exact values,
dtypes and the distinct native and layer validity rules against
independent Rasterio reads. Wider rendering and metadata qualification remains
necessary whenever the selection or mask rules change.

## Production qualification on 2026-10-06

The frozen implementation passes 328 exact window comparisons, 19 grid checks
and eight layout/transport fallback checks. The independent raster boundary
matrix passes 3,646 cases, and rendering passes 996 cases across nearest,
bilinear, cubic and average sampling. The repaired wheel also passes the public
Python/CLI/HTTP, Terracotta compatibility, STAC, multiband layer and request-cost
contracts. Artifact identities and exclusions are recorded in
[human validation](human-validation.md).

Three-trial microchecks compare the default path with `ASYNC_TIFF=false`, using
one worker, no output-array cache and identical returned arrays. First aligned
reads are about 1.4 times faster on a warm local filesystem and 3.6 times faster
against a controlled range origin with 15 ms request latency. Warm aligned
reads are about 12% slower locally and 19% slower over that origin. Statistics
and fallback timings are approximately unchanged. These checks include ABI
serialization and do not establish navigation performance, WAN behavior or a
general throughput improvement. Raw timings and commands remain in ignored
`build/async-tiff-adoption/`.

Qualification covers Linux x86_64 and CPython 3.12. Authenticated providers,
additional platforms and hosted CI remain unexecuted. The local repaired wheel
uses a manylinux 2.39 tag and does not qualify the release portability floor.

## Confirmed TIFF pixel navigation on 2026-10-06

The production HTTP tile path shows a local-storage benefit and mixed remote
results when TIFF pixel access is confirmed. Three alternating paired trials
pass **108 fresh service runs and 13,608 responses**. Explicit backend counters
record **3,690 TIFF pixel-reader invocations and zero GDAL invocations** in the
candidate, and 3,690 GDAL invocations and zero TIFF invocations in the control.
Every viewport is checked for fallback. All **6,804 paired responses**, including
duplicates and revisits, have identical decoded pixels. Independent Rasterio
nearest reads and NumPy stretch also match **2,214 saved-image checks across
153 unique reference tiles**, with no mask or display-channel differences.

Both variants use the same frozen executable and inputs, changing only
`TC_ASYNC_TIFF`. Four workers/serving CPUs, one user and three users with shared
or separate views cover 3-by-3 viewports, three pans, zoom and revisit. Prepared
Sentinel singleband/RGB uses zooms 12–13 and elevation uses zooms 11–12. These
exercise stored base pixels and 2× overviews with nearest sampling. Every imagery
response contains data; median valid coverage is 100% in every workload cell.
Elevation retains 252 transparent responses across both variants and all trials.

The table reports TIFF-enabled changes relative to GDAL. Workload rows weight
the three user cells equally using geometric means of three-trial median ratios.
Throughput divides responses by active viewport time and is not saturation
throughput. Small differences remain subject to local timing variation.

| Workload | Storage | First viewport | Pan | Zoom | Throughput |
| --- | --- | ---: | ---: | ---: | ---: |
| Imagery singleband | Local | 41% faster | 6% faster | 5% faster | 22% higher |
| Imagery RGB | Local | 44% faster | 15% faster | 15% faster | 28% higher |
| Elevation | Local | 44% faster | 21% faster | 16% faster | 31% higher |
| Imagery singleband | HTTP ranges | Approximately equal | 73% slower | 108% slower | 19% lower |
| Imagery RGB | HTTP ranges | 68% slower | 67% slower | 86% slower | 41% lower |
| Elevation | HTTP ranges | 48% faster | Approximately equal | 7% faster | 9% higher |

Across local cells, first viewports are 43% faster and throughput is 27% higher.
CPU usage is 23% lower and peak RSS 8% lower. Across HTTP cells, CPU usage is
25% lower and RSS 9% lower, but throughput is 19% lower. Summed per-cell median
origin GETs rise from 574 to 675 while bytes fall from 55.93 to 53.34 MB.
Higher request counts and imagery latency make HTTP range handling a priority
for further investigation. Cached revisits are approximately unchanged.

These are deliberately eligible inputs. Copies of checksum-pinned real-scene
derivatives remove descriptive TIFF metadata and adjust affine coefficients by
less than **8 nanometers across each footprint** to avoid the strict floating-point
overview guard. Compressed pixel payloads are unchanged; independent comparisons
verify all **6,930 value/mask blocks** across every stored level. Imagery viewports
are placed within visible coverage. A separate initial run with mostly nodata
imagery is retained as a control and excluded from these results. This establishes
the benefit available inside the current eligibility boundary, not the fraction
of ordinary sources that can enter it.

The versioned HTTP origin injects 15 ms latency and enforces `If-Match`. GDAL and
shared output-array caches each receive 64 MiB; encoded-render/source-block caches
are disabled and prefetch is unset. TIFF's additional retention is included in
RSS. Processes are fresh and the OS page cache is warm. Cold disk, browser paint,
WAN/S3, authenticated providers, native-layer/STAC-specific serving and other
platforms remain unmeasured. No decoder, selection rule or default setting changes.

Frozen source/artifact hashes, input recipes, commands, raw trials, explicit
backend counts, independent reference checks and exclusions are retained in
ignored `build/async-tiff-active/`. Its `summary.md` and `report.json` index the
results. Reproduce with its prepared manifest and a configured runtime:

```sh
.venv-reference/bin/python tests/navigation_compare.py \
  --baseline target/release/rcfp --candidate target/release/rcfp \
  --out build/tiff-active-navigation --trials 3 \
  --require-candidate-tiff-pixels \
  --baseline-setting TC_ASYNC_TIFF=false \
  --candidate-setting TC_ASYNC_TIFF=true \
  -- --manifest build/async-tiff-active/manifest.json \
  --storage local range --users 1 3 \
  --setting TC_REPROJECTION_METHOD=nearest --setting TC_RENDER_CACHE_BYTES=0
```

## Ordinary-input navigation benchmark on 2026-10-06

The integration regresses the tested navigation workloads. Across **324 fresh
service runs and 61,236 successful tile responses**, enabling TIFF access
produces identical decoded images. Backend usage was inferred incorrectly from
the shared `raster_read` timer, which both TIFF and GDAL record. The claimed
zero TIFF successes is unsupported; timing, traffic and image comparisons remain
valid. This run does not measure the performance of confirmed TIFF pixel reads.

Three alternating paired trials use the frozen production executable, changing
only `TC_ASYNC_TIFF`. Original Sentinel singleband/RGB, geographic elevation and
WorldCover scenes exercise ordinary source layouts. Existing Mercator derivatives
exercise prepared Sentinel singleband/RGB and elevation. Both use the default
sampling policy, one user and three users with overlapping or separate views,
3-by-3 viewports, three incremental pans, three zoom levels and revisits. Local
files and a strong-ETag HTTP origin with 15 ms request delay are measured
sequentially with four serving CPUs/workers. GDAL and output-array caches each
receive 64 MiB; encoded output and speculative prefetch are disabled. TIFF's
additional worker retention is included in measured RSS.

The table reports the change in viewport completion time with TIFF enabled.
Each row weights workload/user cells equally using geometric means of ratios
between three-trial median timings. Small differences remain subject to local
timing variation; individual cells and raw timings are retained.

| Inputs | Storage | First viewport | Pan | Zoom | Revisit |
| --- | --- | ---: | ---: | ---: | ---: |
| Original scenes | Local | 14% slower | 3% slower | 2% slower | Approximately equal |
| Original scenes | HTTP ranges | 20% slower | 2% faster | 4% slower | 2% slower |
| Prepared Mercator scenes | Local | 15% slower | 2% slower | 4% slower | 3% slower |
| Prepared Mercator scenes | HTTP ranges | 10% slower | 5% faster | 3% faster | 8% slower |

Across those default-sampling groups, aggregate active-request throughput is
2–7% lower and peak service RSS is 4–5% higher. Original-scene HTTP sources use
about 12% more GETs and 5% more bytes; prepared sources use about 3% more GETs
with approximately unchanged bytes. These are successful raster transfers,
excluding missing-sidecar responses. Throughput divides delivered requests by
summed viewport execution time; it is not a saturation test.

Separate nearest-sampling trials include a control that removes only descriptive
`TILING_SCHEME` metadata from a copy of the prepared elevation scene.
Independent Rasterio comparisons verify unchanged
values, masks and geometry in all **513 blocks across six stored levels**.
Its backend usage was not established. An arithmetic replay of the
production selection rules identifies another barrier: the overview guard
rejects nominal scale 2 represented as `1.9999999999999454`. It rejects 54 of
81 viewport requests in the one-user control; the other 27 require upsampling
and cannot use stored pixels. Broad metadata rejection and floating-point
selection limit practical use, independently of decoder performance.

Reproduce a paired production comparison with a configured runtime and the
reference Python environment:

```sh
.venv-reference/bin/python tests/navigation_compare.py \
  --baseline target/release/rcfp --candidate target/release/rcfp \
  --out build/tiff-navigation --trials 3 \
  --baseline-setting TC_ASYNC_TIFF=false \
  --candidate-setting TC_ASYNC_TIFF=true \
  -- --storage local range --users 1 3
```

Use the same `--manifest` for both variants to compare prepared layouts. Leave
`TC_GDAL_OPTIONS` unset so explicit GDAL options do not exclude TIFF access.
The maintained range origin supplies a strong ETag and enforces `If-Match`.
Commands, verified input recipes, per-request results, memory/CPU/traffic,
read-count checks and selection diagnostics are retained in ignored
`build/async-tiff-navigation/`. Its `report.json` and `summary.md` index the
results. Every run uses the same frozen executable from the TIFF qualification;
subsequent workspace changes are recorded separately. Warm filesystem
cache, Linux x86_64, native singleband/RGB catalog routes and controlled HTTP
latency define this evidence; browser paint, native-layer/STAC-specific serving,
WAN/S3, authenticated providers and other platforms remain unmeasured.

## What the probe measures

| Engine | TIFF access | Projection and sampling | Output |
| --- | --- | --- | --- |
| `rcfp` | Worker-owned GDAL datasets with Async-TIFF disabled | Native RCFP reader and reusable GDAL plans | Shared RCFP stretch and PNG encoder |
| `async-native` | Async-TIFF with bounded concurrent range reads and a typed decoded-block cache | PROJ transforms the requested grid; a Rust nearest/bilinear sampler selects overviews and samples pixels | Same encoder |
| `async-gdal` | Same Async-TIFF reader | A selected source window becomes a GDAL MEM dataset with its source dtype, nodata or validity mask; GDAL warps that window | Same encoder |

The HTTP reader implements Async-TIFF's public `AsyncFileReader` trait. It merges
nearby ranges within a request and bounds concurrent reads and decoding separately.
It validates partial-content responses and handles metadata read-ahead at EOF.
Cloud authentication, object-store credential refresh, retries, ETag validation,
and compressed-range caching are outside this adapter. The optional public-URL
mode can read public S3 objects, but it does not exercise authenticated
`object_store` integrations.

Three workloads isolate header inspection, exact native windows at explicitly
selected overview levels, and map-grid reads plus PNG encoding. Tile batches cover
first views, fresh pans, zooms, and revisits. Separate-source imagery is measured
as concurrent band requests. Small derivatives of the pinned Landsat scene add
same-file RGB, internal masks, RGBA, and Web Mercator coverage. Their alpha and
masks come from existing source validity, and their recipes and hashes are
recorded. They are test inputs, not reference goldens.

The custom sampler supports affine, EPSG-identified sources with scalar sample
types and tiled storage. It does not reproduce GDAL's general warp algorithm or
its approximation and overview policy. External masks/overviews, custom and
compound CRSs, GCP/RPC/geolocation, longitude wrapping, fractional alpha, and
cubic/average warp kernels need further work and qualification. Stripped TIFF
headers can be inspected, but pixel reads require a GDAL fallback or tiled
preparation. Fallback is reported rather than silently mixed into candidate
timings.

## Reproduction

Build the independent crate with its own lockfile and target directory:

```sh
CARGO_TARGET_DIR=build/async-tiff-spike/target packaging/cargo.sh build \
  --locked --release --manifest-path tests/benchmarks/async-tiff/Cargo.toml
```

Use the reference Python environment containing NumPy, Rasterio, Pillow and
psutil. Acquire or verify the pinned inputs with `tests/navigation_sources.py`.
Configure the supported GDAL runtime's `TERRACOTTA_GDAL_LIBRARY`, `GDAL_DATA`,
`PROJ_DATA`, and library dependency search path. Set `RCFP_PROBE_PROJ_LIBRARY` to
the matching PROJ shared library. The Python oracle and the two GDAL paths need
that runtime. Async-TIFF window reads and aligned EPSG:3857 sampling do not.

```sh
.venv-reference/bin/python tests/async_tiff_benchmark.py \
  --binary build/async-tiff-spike/target/release/rcfp-raster-probe \
  --out build/async-tiff-spike/evaluation --trials 3
```

The default includes local storage, an instrumented HTTP range origin with 15 ms
per-request latency, all selected original scenes, and the small Landsat probes.
Outputs require a fresh directory. Every source is checked against its manifest
hash before either backend runs. Unsupported requests and sampling differences
remain visible in the report.

For full prepared map grids, use `tests/navigation_prepare.py` and supply its
manifest. Pair both backends on each layout independently:

```sh
.venv-reference/bin/python tests/async_tiff_benchmark.py \
  --binary build/async-tiff-spike/target/release/rcfp-raster-probe \
  --out build/async-tiff-spike/map-grids \
  --prepared-manifest build/map-derivatives/manifest.json \
  --scenes imagery elevation classes south --no-probes --trials 3
```

`--resampling nearest` isolates categorical or nearest sampling. `--merge-gap 0`,
`--io-concurrency 4`, `--inflight 4`, `--cache-mib 0`, and `--overview-bias` allow
focused transport, scheduling, cache and overview experiments. A zero decoded
cache also requires `--curl-cache-mib 0` because the candidate receives the sum
of both configured cache budgets. `--storage public` uses available manifest URLs
and skips layouts without URLs. Controlled range traffic and actual public
storage must be reported separately. The executed public-source matrix uses a
30-second GDAL request timeout, matching the async reader's request timeout:

```sh
GDAL_HTTP_TIMEOUT=30 .venv-reference/bin/python tests/async_tiff_benchmark.py \
  --binary build/async-tiff-spike/target/release/rcfp-raster-probe \
  --out build/async-tiff-spike/public \
  --storage public --scenes imagery elevation classes south --no-probes --trials 3
```

The optional `extra-codecs` Cargo feature enables LERC, LZMA, WebP and JPEG2000
decoders. Its native dependencies and codec inputs require separate qualification.
The executed default build covers the compression methods in the selected
fixtures and does not establish those optional support claims.

## Interpreting evidence

Each trial uses fresh child processes and rotates engine order. Serving runs on
four physical cores by default; the client and range origin use other available
cores. Four CPU permits and sixteen in-flight reads are the default candidate
limits. GDAL has a 64 MiB decoded cache plus a 16 MiB compressed curl cache;
the candidate allocates the same 80 MiB logical total to decoded blocks. Source
metadata, active decoded blocks, projection state and output buffers still have
physical costs. Reports retain PSS/USS/RSS, actual child CPU time, request/byte
counts, decode counts, cache occupancy, stage timings and binary/runtime/source
hashes. Stage timers include waiting for permits and overlap between jobs.

`report.json` retains individual cells and checks. `summary.json` gives median
paired completion ratios and batch request p50/p95/p99. Ratios above one favor
the candidate. A phase with an execution error is excluded from its completion
ratio. Fidelity differences do not disappear from the report when a phase is
faster. Request latency includes waiting for batch admission; it is an internal
raster-pipeline measurement rather than HTTP or browser latency. Startup is
reported separately, and artifact writes occur after each timed batch.

Independent public GDAL/Rasterio reads supply the expected values, masks, shapes,
source dtypes, affine grids, and rendered stretches. The runner retains these
expectations separately from candidate arrays and PNGs. Exact matches, numerical
or mask differences, execution failures and unavailable reads have separate
counts. It never regenerates maintained golden outputs from RCFP.

A useful adoption gate is improved representative remote navigation with equal
logical budgets and acceptable physical memory, followed by the existing native
rendering and broader raster matrices. Header/window gains alone do not justify
a renderer replacement. The named compatibility profile, statistical metadata,
real credential handling, HTTP admission/cancellation, wheel/Lambda packaging,
and other platforms remain separate qualification requirements.

## Execution evidence

Linux x86_64 results are retained in
`build/async-tiff-spike/execution-20261006/`. All three engines use the same frozen
executable, SHA-256
`8f86b170dbe5fb6800748f54bb5b583226ba10713115e0d36cdf3b034a69e93c`.
`build-inputs.json` retains the build snapshot; individual reports also record
source hashes at run start. `results.json` indexes the results and exclusions.

The completed matrices contain **171 cells**:

- `paired/`: 108 cells across three rotating trials, local/range storage,
  original/prepared layouts, and all three workloads.
- `coverage/`: 36 cells covering the default originals and the real Landsat
  RGB, mask, RGBA and Mercator derivatives, locally and over ranges.
- `public-bounded/`: 27 cells across three rotating trials on public S3,
  using all three workloads and a 30-second GDAL request timeout.

Originals in the paired and public matrices cover Sentinel imagery,
northern/southern DEMs and WorldCover. The prepared manifest covers imagery and
both DEMs. Both sides use identical inputs within each layout. Range measurements
use the 15 ms controlled origin. These are local process/cache-cold results on
warm filesystem inputs, with CPU affinity rather than a dedicated host.
Public network, origin and intermediary caches are uncontrolled.

Both candidates pass all **582 header/window checks each** in the paired matrix.
Async-native cold headers over controlled ranges improve 7.84× on originals
and 8.53× on prepared COGs. Cold native-window batches improve 2.40× and 2.06×,
respectively. Local window batches and several warm remote phases regress.
Header inspection does not compute ingestion statistics or percentiles.

On public S3, each candidate passes all **162 header/window checks**. Async-native
cold headers improve 1.21× and cold windows 1.52×; the hybrid improves 1.20× and
1.56×. These smaller gains show why the controlled origin is insufficient for
predicting actual storage performance.

Tile completion ratios below are median baseline/candidate ratios. Ratios above
one favor the candidate. Fidelity differences remain separate, and phases with
failed reads are excluded from comparisons.

| Candidate | Layout | Storage | First | Pan | Zoom | Revisit |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| async-native | Original | Local | 0.48 | 0.45 | 0.31 | 0.45 |
| async-native | Original | Range | 1.26 | 0.84 | 0.94 | 1.11 |
| async-native | Prepared | Local | 0.54 | 0.64 | 0.46 | 0.48 |
| async-native | Prepared | Range | 1.62 | 1.56 | 1.37 | 1.12 |
| async-gdal | Original | Local | 0.36 | 0.44 | 0.36 | 0.41 |
| async-gdal | Original | Range | 1.01 | 1.08 | 0.97 | 0.92 |
| async-gdal | Prepared | Local | 0.23 | 1.31 | 0.85 | 1.02 |
| async-gdal | Prepared | Range | 0.60 | 2.70 | 2.07 | 2.05 |
| async-native | Original | Public S3 | 1.03 | 7.64 | 8.39 | 16.02 |
| async-gdal | Original | Public S3 | 1.10 | 9.09 | 11.46 | 12.38 |

Public S3 median first views are 7.44 seconds for RCFP, 7.04 for async-native
and 6.82 for the hybrid. Median new-pan batches are 2.39 seconds, 0.30 and
0.30 seconds, respectively. Each candidate has one unavailable tile request
in its first trial, recorded as a request-send error. Its affected zoom batch
is excluded, leaving five successful zoom groups per candidate. All RCFP tile
requests pass their independent checks. Network variability and the retained
transport failures limit confidence in generalizing these substantial warm
navigation gains. The adapter still needs retry and credential qualification.
Public baseline traffic is uninstrumented; zero external GET/byte counters in
those report rows mean unavailable measurements.

The original/range native candidate uses a median 414 MiB peak PSS versus
314 MiB for RCFP and 10.07 versus 4.40 seconds of child CPU, despite transferring
168 versus 260 MiB over the navigation sequence. On prepared/range inputs,
async-native uses 264 versus 465 MiB PSS and transfers 122 versus 246 MiB, but
still uses 5.50 versus 3.35 seconds of CPU. The hybrid uses 619 MiB PSS and
transfers 513 MiB on that layout. On public S3, both candidates use roughly
11.4 seconds of CPU versus 6.1 for RCFP. Logical cache equality does not account
for transient buffers or representation costs. Planning, sampling, window
assembly and duplicated active decoding remain substantial prototype costs.
These measurements do not establish limits on Async-TIFF itself.

Both candidates exactly match all 72 categorical requests in each original
tile cell. Continuous rendering needs further work. For one original local
trial, async-native differs at 48 mask/alpha pixels and has a greatest per-image
p99 channel difference of 18/255. The hybrid has exact original masks/alpha and
source dtypes, but still reaches p99 2/255. Prepared inputs expose 1,503 differing
mask/alpha pixels for async-native and 1,197 for the hybrid. These differences
repeat across paired runs and prevent an adoption claim. RCFP's prepared raw
arrays have tiny floating differences from independent reads in 52 requests
per cell, while its masks, dtypes and decoded PNGs match exactly. Those raw
differences remain reported.

Coverage runs pass all supported header/window checks and complete all tiled
RGB/mask/RGBA requests, with sampling differences retained. Stripped TIFF pixel
reads are unavailable in 30 async-native and 28 hybrid requests. No process
fails in the three completed matrices. An additional public attempt in `public/`
retains 18 completed header/window cells, two response-body errors and a
300-second RCFP tile-process deadline failure in `execution-failure.json`.
That attempt supplies no tile performance evidence.

`no-native-runtime-result.json` records 45 prepared-grid requests completed by
async-native with the GDAL/PROJ runtime environment removed. The corresponding
`openat` trace contains no GDAL or PROJ library opens. This demonstrates that the
aligned COG path can run without those libraries; it does not qualify its
fidelity or a distributable GDAL-free package.

The independent crate passes a release build, Rust formatting and locked,
offline warnings-as-errors Clippy. The Python runner passes Ruff and byte
compilation. Its isolated lockfile includes current core dependencies without
changing the production graph at the time of those measurements. Optional
codecs, authenticated cloud stores and other platforms remain unexecuted.
The evidence supports further work on async transport and shared decoded
caching. Sampling fidelity and transport resilience need improvement before
replacing the general renderer.
