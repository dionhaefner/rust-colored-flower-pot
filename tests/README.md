# Test suites and fixtures

The [public raster corpus](fixtures/README.md) maps real inputs to independent
fidelity and performance checks. Run its offline integrity gate with NumPy and
Rasterio, then the controlled failures:

```sh
python tests/corpus_contracts.py --out build/corpus-integrity
python -m unittest discover -s tests -p 'test_corpus_contracts.py'
```

`upgrade_contracts.py` compares two exact installed wheels in separate processes
and rehearses compatible SQLite reads/writes, previous-version restart, committed
WAL backup, and restore. Both environments need Pillow. It checks installed
package files against the supplied wheel before running, requires distinct
artifacts, and records failures and excluded deployment/adopter scopes:

```sh
python tests/upgrade_contracts.py \
  --previous-python /path/to/previous-env/bin/python \
  --previous-wheel /path/to/previous.whl \
  --candidate-python /path/to/candidate-env/bin/python \
  --candidate-wheel /path/to/candidate.whl \
  --out build/application-upgrade
```

This rehearsal uses the Terracotta profile and legacy SQLite format. Native
keyed schemas, explicit format migrations, remote providers and real deployment
need their own checks. [Application evaluation](../docs/how-to/evaluate.md) and
the [maintenance plan](../design/adoption.md) distinguish automated checks from
independent adoption and retained production usage.

Tests are organized by the interface they exercise. The Python end-to-end
runners are standalone scripts that write JSON reports. The installed-wheel
contract runner also writes JUnit XML. They are not collected by `pytest`.

Keep assertions, required case manifests, fixture provenance, and pinned oracle
expectations in this directory. Write generated outcomes to fresh ignored `build/`
directories. CI retains them as run artifacts. A saved outcome does not establish
that the current source or artifact passes. The native, wheel, correctness, and
documentation workflows are configured to rerun checks on pull requests and
pushes to `main`. Expensive and environment-specific qualification is described
in the [validation handoff](../design/human-validation.md).

| Layer                         | Entry point                                                                                                                 | CI lane                                 | What it checks                                                                                                                                                                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rust unit tests               | `cargo test --locked`                                                                                                       | Native platforms                        | Core parser, expression, raster, settings, and storage logic on five OS/architecture cells.                                                                                       |
| Native profile corrections    | `python tests/native_contracts.py --binary BINARY --out OUT`                                                                | Native platforms                        | Both profiles: ingest validation/rollback, stale metadata, signed extrema, rotated validity hulls, percentile endpoints, empty dimensions, HTTP errors/specification, and modulo. |
| Rust SQL/remote integration   | `cargo test --locked --features storage-integration --test storage_sql --test storage_remote`                               | Correctness services                    | Disposable MySQL/PostgreSQL databases and remote storage with real services.                                                                                                      |
| Installed-wheel end-to-end    | `python tests/contract_suite.py --wheel WHEEL --out OUT`                                                                    | Wheels: 5 platforms × 3 Python versions | 53 required public Python, WSGI, CLI, HTTP, raster, metadata, and SQLite cases.                                                                                                   |
| Terracotta compatibility      | `RCFP_COMPAT_PROFILE=terracotta-0.10.1 python tests/compatibility_contracts.py --wheel WHEEL --out OUT`                     | Wheels: 5 platforms × 3 Python versions | Required legacy Python/CLI/WSGI and catalog contracts. Linux also replays against pinned Terracotta using `--reference-python`.                                                   |
| Other standalone end-to-end   | `tests/storage_contracts.py`, `remote_contracts.py`, `browser_contracts.py`, `platform_smoke.py`, `lambda_runtime_smoke.py` | Correctness, wheels, native platforms   | Live databases, HTTP ranges, Chromium UI, real raster, and official local Lambda runtime.                                                                                         |
| Large-file and resource tests | `tests/large_raster_contracts.py`, `resource_contracts.py`                                                                  | Scheduled, manual, or tag correctness   | Physical BigTIFF reads above 4 GiB and concurrent server resource behavior.                                                                                                       |
| Pinned reference comparison   | `tests/reference/differential.py`, `upstream.py`, `compatibility.py`                                                        | Correctness and wheels                  | 26 candidate comparisons, selected documented driver cases, and the pinned reference test baseline.                                                                               |

`rendered_cache_contracts.py` verifies publication and conditional responses
across the installed Python wheel, WSGI, native HTTP and Lambda. On Linux,
run it with `--binary`, a fresh `--out`, and optionally `--nginx /usr/sbin/nginx
--browser` to exercise the downloadable proxy/browser examples. Three paired
nine-tile trials measure encoded retention, conditional bandwidth, RSS and
duplicate work using checksum-pinned real pixels with explicit Mercator geometry.
Reports list unavailable nginx/Chromium checks separately. The Linux x86_64
CPython 3.12 wheel job runs the complete suite.

The demo's credentials-free runtime and Pages checks are in
`tests/demo_contracts.py`. Run them with `--image rcfp-demo` after building the
demo image, or `--binary dist/bundle/bin/rcfp` for the native Runtime API adapter.
The [demo guide](../docs/how-to/demo.md) describes deployment and qualification.
The independent preview in `fixtures/demo/` was acquired with Terracotta 0.10.1
and GDAL 3.10.3 using `reference/acquire_demo.py`. Its manifest pins the raster
and image checksums. Never replace it with output from RCFP.

Reference setup, commands, and classified exclusions are documented in
[reference/README.md](reference/README.md).

The [broader raster matrix](reference/README.md#broader-raster-correctness)
adds pinned polar/dateline DEMs, rotated grids, dataset masks/RGBA, unusual CRSs,
overviews, source edges, and all 16 supported warp/read method pairs. It checks
both profiles through the freshly built shared core and writes per-case failures
without benchmarks. Its acquisition manifest is `fixtures/geography-sources.json`.
Generated derivatives and results stay in ignored storage. The correctness
workflow runs the same general-reader matrix, with native direct reads explicitly
disabled, and retains its report separately.

Run `python tests/build_errors.py` with pip and the Hatch build dependencies
installed to check Linux build failure diagnostics through wheel and editable
metadata hooks. A deliberately failing Cargo executable checks that the original
error and exit status remain visible without an unrelated backend traceback.

`python tests/license_contracts.py` checks shared runtime notices and platform
Cargo notice collection without compiling or downloading dependencies. Wheel
qualification also requires the native and embedded-asset inventories in the
final repaired artifact.

The correctness services job publishes Rust source line coverage from its unit,
SQL/remote integration, and instrumented native catalog operations. The Linux
x86_64 / CPython 3.12 installed-wheel job publishes separate Python adapter line
and branch coverage from the in-process contract suite. GitHub job summaries show
the totals and lowest-covered files. Downloadable artifacts contain Rust LCOV and
Python JSON/XML. These numbers exclude wheel native extension, browser, Lambda, and other
platform runs. Terminated HTTP server subprocesses may not flush Rust counters.
Coverage is reported as a baseline, with no percentage gate until hosted
reports establish a stable value.

`coverage-summary.py` renders those Rust LCOV and Python JSON reports for GitHub
job summaries. `ci-gdal-env.py` records the test environment's conda GDAL library
and data paths for subsequent CI steps. `verify-runtime-without-rasterio.py`
checks the installed wheel's native discovery, rendering, and CLI after the smoke
fixtures have been prepared and Rasterio, NumPy, and Pillow have been removed.

## Real-raster smoke fixtures

`platform_smoke.py` exercises the native binary and installed wheel against a
real Sentinel-2 red-band crop. `fixtures/platform-smoke.json` pins the source URL,
byte count, SHA-256, native pixel window, CRS, transform, decoded pixel SHA-256,
and attribution. The parent TIFF is downloaded only if the ignored local crop is
missing. Every run verifies the crop's decoded pixels and geometry.

`fixtures/lagoon-red-preview.png` is a Terracotta 0.10.1 rendering of that
crop at the smoke's explicit stretch. The smoke checks decoded RGBA pixels, not
PNG compression bytes. It also exercises installed RCFP discovery, metadata,
RGB, compute, invalid-query, CORS, WSGI, CLI and HTTP paths. The native lane
covers catalog creation, insertion and Lambda event rendering.

`lambda_runtime_smoke.py` uses the SQLite catalog created by the native smoke
and verifies the packaged Lambda runtime in the official local image. It
requires Docker and a built Lambda zip. A Python syntax pass is not a
runtime-image pass. The Rust expression vectors in `fixtures/` are retained
contract examples used by the unit suite. Rust storage tests create disposable
SQLite databases rather than depending on a preexisting database.

The optional `storage-integration` Cargo feature runs disposable engine tests.
Set `RCFP_TEST_MYSQL_URL` and `RCFP_TEST_POSTGRESQL_URL` to database server
URLs before enabling it. The tests create their own database names.

## Installed-wheel contract suite

`contract_suite.py` runs the public Python, WSGI, CLI, HTTP, raster, metadata,
and SQLite workflows against the installed wheel. It accepts the tested wheel
path with `--wheel` and writes JSON and JUnit reports with the wheel SHA-256.
The checked-in `fixtures/contract-cases.json` lists every required case ID.
Removing or skipping a case fails the suite. NumPy supplies the compatibility
API’s masked-array return type. Pillow is a
test-only image decoder. Flask and Click are not runtime dependencies. The native
API can run without importing NumPy.

`compatibility_contracts.py` has its own required case manifest and artifact hash.
It verifies profile precedence and immutability, masked arrays and Futures,
named exceptions, lazy metadata and connection contexts, the WSGI entry point, every
CLI workflow, migration dry runs, and rejection of unsupported catalog formats.
Reference replay compares decoded images, metadata, types, and discovery in
both directions using catalogs created by each implementation. The existing
HTTP differential and live provider suites also run with the compatibility
profile selected. Only confirmed answer-changing optimizations are disabled.
Rust policy tests keep exact fast reads and prefetch available.

The live `compatibility_sql.py` test additionally needs SQLAlchemy, PyMySQL and
psycopg2-binary to administer disposable reference databases. Install those in
the test environment. They are not RCFP runtime dependencies.

`fixtures/corpus/` contains seven 512-pixel native windows of Sentinel-2,
Copernicus DEM, and ESA WorldCover source rasters. Their manifest records exact
parent URLs and hashes, pixel windows, TIFF hashes, pixel/mask hashes,
coordinate transforms, geographic bounds, and attribution. The crops retain
source values and masks. They were written as DEFLATE GeoTIFFs without warping,
resampling, or stretching. The suite verifies every fixture before it builds a
catalog. Keep the manifest and files together when changing the corpus.

`storage_contracts.py` drives the native executable through catalog operations
and live HTTP on disposable SQLite, MySQL, and PostgreSQL databases. It checks
transaction rollback and compares decoded images across backends. Set
`RCFP_TEST_MYSQL_URL` and `RCFP_TEST_POSTGRESQL_URL` for the SQL run. Use
`--sqlite-only` for local feedback without server containers. Disposable
plaintext loopback servers need `?sslmode=disable` in each URL. Remote servers
use verified TLS by default.

`native_contracts.py` checks deliberate native corrections against separate legacy
expectations, using the same binary and fresh per-profile catalogs. It verifies
the pinned corpus hashes, derives rotated opaque/masked windows from real red-band
pixels without resampling, and checks affine corners independently. Tiny generated
signed/constant rasters isolate extrema unavailable in the retained real corpus.
They are edge inputs, not replacement reference goldens. Reports identify the
tested binary hash and every profile/case result. The SQL suite additionally checks
native default MySQL singleband/RGB stretches, compatibility failures, and metadata
invalidation/lazy fill on every engine.

Both profiles also run a short eight-client render admission check with three
raster workers and a 64-job queue. The full 180-second resource suite remains a
separate scheduled/manual gate. Compatibility wheel contracts verify that SQL
credential/TLS URL changes obtain different drivers. Live provider round trips
warm a connection, reject an incompatible TLS mode through another driver, and
check that the original connection still works.

`remote_contracts.py` compares local TIFF metadata and image pixels with the
same fixture served by a loopback HTTP server that records byte-range requests.
It runs against each installed platform wheel and fails when GDAL silently
reads the whole object instead of requesting ranges.

`browser_contracts.py` launches the bundled viewer and all three independent SDK
maps against a real native HTTP server in both profiles. It verifies discovery,
metadata/legends, singleband/RGB/expression tiles, display point inspection,
explicit catalog time browsing, revisioned state/reload, Unicode/escaped-slash
keys, JSON preflight/CORS, errors/recovery, fetch abort and stale-view responses,
keyboard controls and mobile layout. It blocks outbound requests and retains
screenshots, exact executable hash and pinned fixture provenance. Time labels
reuse a real raster to test browser selection, not temporal acquisition science.
External provider basemaps and source-value point APIs are separate scope.

Run `node tests/sdk_contracts.mjs` for SDK transport, paging, revisions, encoding,
adapters and cancellation controls. Check public declarations using
`tsc --strict --noEmit --module nodenext --target es2022 tests/sdk_types.mts`.
Use Node 22 and TypeScript 5.9.3. The vendored browser libraries have SHA-256 locks
in `src/assets/client/vendor/manifest.json`, verified by the browser runner.

SDK qualification also tests Node 20/22/24, release tag/version rules and the
installed npm tarball. Install the locked TypeScript tooling with
`npm ci --prefix packaging/sdk --ignore-scripts --no-audit --no-fund`, then run
`node tests/sdk_release.mjs` and `node tests/sdk_package.mjs build/sdk-package-check`.
The package runner checks the exact file boundary and source bytes before compiling
and executing a consumer of both public exports. Use a fresh output directory.
The reusable SDK workflow runs through correctness qualification and independent
SDK releases. See [testing and releases](../docs/how-to/release-sdk.md).

`python tests/sdk_versioning.py` uses Hatch VCS in a temporary Git repository to
check that interleaved stable/prerelease SDK tags preserve Python VCS versions.
Install `hatch-vcs==0.5.0` to run it.

`large_raster_contracts.py` acquires the checksum-pinned full ESA WorldCover
source when needed, writes a physical tiled uint32 BigTIFF, checks all 5,041
source blocks and masks, and compares rendered tiles from the original, local
derivative, and a byte-range HTTP server. It requires an actual range request
above 4 GiB. `resource_contracts.py` repeatedly requests a real tile with
concurrent clients, checks image stability and error recovery, and bounds RSS
and file descriptor growth. These larger checks run on scheduled, manual, and
tag qualification. Their reports are retained without uploading the multi-GB
fixture.

## Real map-navigation performance

`service_benchmark.py` compares native RCFP, Terracotta and TiTiler/rio-tiler on
the full checksum-pinned navigation inputs. Create separate CPython 3.12
environments with `benchmarks/terracotta.lock` and `benchmarks/titiler.lock`.
The latter includes the test-only image decoder. Configure the native GDAL
runtime and acquire originals with `navigation_sources.py` first:

```sh
python tests/service_benchmark.py --binary target/release/rcfp --reference-python /path/to/terracotta-env/bin/python --titiler-python /path/to/titiler-env/bin/python --out build/service-comparison --trials 3
python tests/service_benchmark_quality.py --results build/service-comparison --out build/service-quality
/path/to/terracotta-env/bin/python tests/service_benchmark_environment.py --engine terracotta --out build/service-comparison/terracotta-environment.json
/path/to/titiler-env/bin/python tests/service_benchmark_environment.py --engine titiler --out build/service-comparison/titiler-environment.json
```

The runner is Linux-specific and requires four physical serving cores. Clients
and the instrumented range origin use remaining physical cores where available.
Three rotating-order, fresh-process trials cover singleband imagery, elevation,
categories and three-source RGB, local/range storage, one/three overlapping or
independent users, first views, pans, zooms, revisits, stretch changes and a
two-source ratio expression. HTTP completion is recorded before client image
decode. Requests are never retried out of the results. Service process-tree
PSS/USS samples include the Python masters/workers. Total logical cache budgets
include each worker's GDAL, array and curl caches. Encoded-image retention is
off. Reports preserve every request, setting, source hash, latency and source
traffic count. Generated evidence stays in ignored `build/`.
Native source leases default to 60 seconds in this runner. Pass
`--rcfp-source-ttl 0` to reproduce published trials with zero retention. Active
identical RCFP renders coalesce even with zero finished-image retention.
The environment recorder hashes the actual installed application/raster code and
native Rasterio dependencies, including an editable Terracotta reference.

`benchmark_titiler_app.py` is a test-only SQLite-to-STAC adapter around TiTiler's
normal multi-asset factory/reader. It selects the same assets, 256-pixel PNGs,
compression level, numeric stretches and category palette. Outside-source tiles
follow the common transparent-image policy. It uses rio-tiler's stretch to the
1–255 valid-value range. Integer truncation remains rio-tiler's own behavior.
The saved-image quality runner checks independent GDAL output grids, dimensions,
coverage and category colors and reports sampling/rounding differences. It does
not treat any service output as a golden or relabel differences as passes.
These comparisons leave cold disk, browser paint, actual S3, genuine temporal
acquisitions and dynamic mosaics as explicit applicability limits. Deadline and
disconnect behavior is checked separately by `request_lifecycle.py` as correctness,
without cross-service timing comparisons.

`navigation_sources.py` acquires the complete, checksum-pinned Sentinel-2,
WorldCover, northern/southern DEM and multiband Byte imagery listed in
`fixtures/navigation.json`. Keep large originals in ignored `.cache/corpus/`:

```sh
python tests/navigation_sources.py
python tests/navigation_sources.py --offline
python tests/navigation_benchmark.py --binary target/release/rcfp --out build/navigation --storage local range --users 1 3
```

Use a configured native GDAL runtime (library/dependencies/GDAL_DATA/PROJ_DATA).
The maintained benchmark creates fresh SQLite catalogs/services, prepares
metadata outside timings, verifies full source hashes, and drives fixed 3×3
viewports, incremental pans, zooms and revisits. It records each response,
receipt/completion times, decoded images, native metrics, CPU/RSS and source
range counts/bytes. One/three users exercise overlap and separate views. Defaults
use four cores/workers and four parallel requests, GDAL 64 MiB and array 64 MiB.
`--setting NAME=VALUE` overrides controls explicitly. `--storage s3-public` uses
actual public URLs. Range mode injects 15 ms per source request and is a surrogate.
A timeout/non-200 fails the run and remains in its report. Physically cold disk,
WAN/browser paint and private credential refresh require separate environments.
The controlled range origin supplies a checksum-based strong ETag and enforces
`If-Match`, allowing version-aware readers to participate. It disables Nagle on
accepted sockets so separate header/body writes do not add packet delays to the
configured origin latency. Per-viewport reports
include request throughput and source-traffic deltas.

`--require-tiff-pixels` requires at least one production TIFF pixel read and
fails if any pixel read invokes GDAL. Per-viewport metrics record explicit
`cost.raster_backend_reads` counters after the timed requests finish. They count
reader invocations, including unsuccessful attempts, rather than pixels or
cache hits. The shared `raster_read` timer cannot identify the backend. Paired
comparisons accept `--require-candidate-tiff-pixels` to apply this gate only to
the candidate. Choose eligible inputs and sampling settings explicitly.

For three sequential alternating paired trials against a frozen baseline:

```sh
python tests/navigation_compare.py --baseline build/baseline-rcfp --candidate target/release/rcfp --out build/navigation-paired --allow-image-change --candidate-setting TC_REMOTE_SOURCE_CACHE_TTL_SECONDS=60 --candidate-setting TC_RENDER_CACHE_BYTES=16777216 --candidate-setting TC_REMOTE_RENDER_CACHE_TTL_SECONDS=60 --candidate-setting TC_RASTER_CACHE_BYTES=50331648 -- --storage local range --users 1 3
```

This divides the same 128 MiB aggregate logical GDAL/application budget between
arrays/images. Do not run other timing experiments on its pinned cores. Preserve
individual cells and failures. `--allow-image-change` requires independent native
quality qualification and does not turn the candidate into its own oracle.
Image hashes establish equality across exact optimizations, not geographic truth.

`navigation_prepare.py --binary ... --out build/map-derivatives --block-size 1024`
prepares full map-grid derivatives and a separate bundled-RGB manifest, retaining
original navigation bounds and build-time/storage costs. Feed its
`manifest.json`/`multiband-manifest.json` to paired comparisons with
`--candidate-manifest`. Fixture preparation never writes reference goldens.
Smaller blocks and bundled RGB can regress remote/singleband workloads.
`--candidate-rgb-format webp --allow-image-change` compares RGB display codecs.
Paired checks additionally require exact alpha and dimensions. Native PNG versus
WebP timing/bytes are separate from `display_encoding_benchmark.py`, a Pillow
feasibility probe on real captured tile inputs.

`native_rendering.py --binary ... --out build/native-grid` checks tiles against
independent public GDAL warp/Rasterio reads. It requires navigation originals
plus the pinned sources in `fixtures/geography-sources.json`, stored under
`.cache/raster-geography/`. Reproduce nearest, cubic and average with
`--resampling`. The default is bilinear. The full matrix checks exact affine grids,
coverage, category colors, continuous channels within one byte, polar/dateline,
rotated real source pixels, unaligned Mercator, southern UTM, overviews, rectangular dimensions,
multiband RGB, shared external masks, external overviews, nodata-free four-band
pruning, three-axis vertical-unit fallback and WebP coverage (249 checks per resampling policy). A real-value DEM with a synthetic fractional
alpha edge isolates floating alpha semantics. Reports identify every check and
binary hash. The crop/derivative recipes are test inputs, not Terracotta goldens.

`navigation_cache_contracts.py --binary ... --out build/cache-freshness` verifies
cache hits bypass raster execution, dimension keys, external SQLite
metadata/deletion, raster writes, and external mask creation/replacement. Unit
checks cover TTL/eviction/coalescing/failure cleanup and queued cancellation.
Run `resource_contracts.py` with `TC_NATIVE_DIRECT_READ=true` to exercise sustained
direct-grid reads. Image retention stays off in that execution-accounting test.
The resource runner uses four output grids and eight clients to exercise both
concurrent native work and identical active-request sharing. Its execution
accounting includes coalesced followers.

The ignored Rust `image_benchmark` test isolates lossless PNG compression. Set
`RCFP_IMAGE_BENCHMARK_INPUTS` to a JSON array of captured navigation PNG paths and
run `cargo test --release --test image_benchmark -- --ignored --nocapture`. Retain
the originating navigation report with fixture/binary hashes and SHA-256 hashes
of the captured files. The probe additionally records input CRCs. Captures are
timing inputs, never reference goldens. It checks decoded channels/transparency
before timing, reports level 1 once and level 6 with the optional compressor
on/off. Library availability and native runtime identity must accompany timings.

These focused native checks do not close the broader existing metadata/preview
boundary matrix or qualify every remote provider/platform.

`async_tiff_benchmark.py` drives the independent Rust crate in
`benchmarks/async-tiff/` against the current RCFP reader. It checks checksum-pinned
headers, exact native windows/overviews, and nearest/bilinear map-grid reads with
PNG output. Local and instrumented range trials record timing, traffic, memory,
and independent GDAL/Rasterio fidelity checks. Generated Landsat derivatives add
internal masks, RGBA and same-file RGB. The crate has its own pinned Async-TIFF
dependency and lockfile. Its GDAL control disables production Async-TIFF access. See the
[evaluation notes](../design/async-tiff-evaluation.md) for build commands, prepared
COG comparisons, result interpretation, and remaining qualification limits.

`.venv-reference/bin/python tests/tiff_backend.py --out build/tiff-qualification`
prepares casts/copies of the
checksum-pinned corpus and compares exact values, masks and dtypes with Rasterio.
It exercises local and loopback HTTP ranges, base/overview selection, the public
reader, sidecar/revision invalidation, transport fallbacks and allocation limits.
It requires the reference Python environment and supported GDAL runtime. The
runner executes fixture-dependent Rust tests that ordinary `cargo test` skips.
Format derivatives use an explicit Mercator grid to isolate pixel selection.
Three nonfinite pixels and malformed directories are narrowly synthetic edges.
The broader native-rendering and raster-boundary suites cover sampling/fallback
behavior. Prepared CI entries are not evidence of execution.

## Request cost and contention

`request_lifecycle.py` is the focused correctness gate for native HTTP in both
profiles. It holds checksum-pinned real raster reads at a controlled range origin
and checks response deadlines without losing active-work accounting, expired queued
work never reaching the source, bounded 503 rejections before and after deadline
expiry, socket-disconnect accounting, eventual drain and successful subsequent
renders. It records status and accounting snapshots rather than performance
ratios. It needs only Python's standard library and the candidate GDAL runtime:

```sh
python tests/request_lifecycle.py --binary /path/to/rcfp \
  --out build/request-lifecycle-check
```

The native platform workflow runs this gate and retains its report and logs.
Configured CI is not execution evidence. The request-cost gate below exercises
native signal draining and Python capsule joining while a real read is blocked.

`request_cost.py` checks native HTTP and a persistent Python `NativeApplication`
against the checksum-pinned Sentinel crop behind a controlled range server.
It verifies stage histograms, source-handle observations, protected JSON and
Prometheus routes, X-Ray worker-stage propagation and route redaction, slow/error
sources, expired queued waits, active-work accounting, duplicate render sharing,
socket disconnect drain, and capsule destruction while GDAL is executing. The
Unix signal gate closes native HTTP admission on SIGTERM/SIGINT/SIGHUP, waits for
the blocked native read and rejects obsolete queued work, including a wildcard
listener. Windows console shutdown remains a separate platform check. The
Python extension must come from the candidate build. The test releases actual
capsules rather than simulating shutdown. It also runs three alternating paired
foreground/prefetch trials with equal worker/cache budgets and unchanged pixels,
recording p95 separately from correctness and accounting passes.

```sh
python tests/request_cost.py --binary /path/to/rcfp/runtime/bin/rcfp \
  --python-package /path/to/installed/site-packages --out build/request-cost-check
```

Run with the candidate's installed Python environment. The range server delays
real fixture HEAD requests. No synthetic raster or generated oracle is used.
Origin request/byte counts are reliable for that controlled server only. The
native subprocess uses SIGTERM cleanup. Graceful native joining is tested through
both Unix signals and persistent Python capsule destruction. OS/release, actual S3/WAN and browser
qualification remain separate. Reports record artifact/input hashes and those
exclusions. The maintained resource soak complements this short contention gate:

```sh
python tests/resource_contracts.py --binary /path/to/rcfp --seconds 180 \
  --out build/request-resource-check
```

`request_priority.py` measures successful four-request foreground viewports while
duplicate or expired work contends for one raster worker. It runs three alternating
pairs per workload, checks admitted/completed/cancelled job counts, compares image
bytes and records foreground p95, median viewport time, sampled RSS and descriptors.
Both arms use zero array/image retention and the same queue and response deadlines.
The source serves pinned real pixels. Obsolete-source HEAD requests have a controlled
75 ms delay. Loopback measurements do not establish production or WAN latency.

Supply candidate and control executables built from one frozen source tree. The
coalescing control calls the render closure directly at zero image-cache capacity
in `RenderCache::render`. The queued-expiry control removes the two worker checks
that discard jobs with uninterested waiters. Keep these copies and their source
hashes in ignored storage. The runner verifies each control's job counts and
retains artifact hashes. It requires loopback sockets and Linux `/proc` sampling.

```sh
python tests/request_priority.py --candidate /path/to/candidate/rcfp \
  --without-coalescing /path/to/coalescing-control/rcfp \
  --without-queued-expiry /path/to/queued-expiry-control/rcfp \
  --out build/request-priority-check
```

## Native database-backed multiband layers

`python tests/layer_contracts.py --binary BINARY --out OUT` checks self-contained
SQLite layer definitions, source band metadata, packed/separate RGB, multispectral
raw/scaled arithmetic, per-band nodata/masks and fractional alpha, mixed resolutions, EPSG grids,
category colors, overlap/selection, invalid inputs, import/rollback, CLI and real
HTTP decoded images against independent Rasterio/NumPy references. Inputs derive
from the checksum-pinned corpus. Declaration/alpha edge cases are documented in
the runner. No service output becomes a reference golden. Add `--wheel WHEEL`
with that exact wheel installed to check Python masked samples, cache identity
and WSGI. Native-only runs record those unavailable checks separately.

## STAC provider and immutable asset selections

Run `python tests/stac_serving.py --binary BINARY --wheel WHEEL --out OUT`
to verify automatic `--source` discovery, local deployment configuration, shared
Python/WSGI/Lambda applications, CLI Item/ItemCollection/Catalog/Collection/API
sources, shared catalog
filtering/paging/metadata, singleband/RGB/compute rendering, asset discovery,
request-time bands/stretch, scale/offset, private refresh and HTTP methods/CORS.
XYZ samples and coverage use independent GDAL reads on pinned Sentinel crops.
Previews are checked for byte equality with database-backed rendering through
the public CLI and HTTP routes. Add `--browser` with Playwright/Chromium installed
to verify the packaged `serve` → `connect` map, asset switching, zoom and display
inspection without external requests. Browser checks omitted from a run are
reported separately. Static graph checks cover nesting, shared/cyclic links,
canonical relative paths, published self links, empty graphs, credential refresh
on later documents, origin restrictions and document/Item/byte limits. Synthetic
JSON whitespace exercises byte budgets without changing raster reference samples.

Run `python tests/stac_contracts.py --wheel WHEEL --out OUT` in the installed
wheel environment with NumPy, Pillow and Rasterio available. Its required
cases exercise a live local POST/GET STAC API provider, pagination/limits/errors,
credential/signature refresh and privacy, revision isolation and independent
real-raster display/coverage checks. The real Sentinel red/green packed-band
fixture is derived without sample/grid changes, with its hash/recipe retained
in the report. Synthetic inputs are limited to protocol responses, malformed
metadata and a nested-VRT rejection edge. No synthetic pixel golden is used.

The runner separately measures indexed provider selection at 1000, 100,000 and
1,000,000 rows, using five trials and retained query plans, then records first
asset verification/render and repeated render. It does not crawl the catalog or
read rasters during selection. Reports include the tested wheel hash, every case,
measurements, explicit exclusions and skips. Hosted PgSTAC, cloud SDKs and WAN
transfer performance remain separate qualification. The wheel workflow executes
this runner on its CPython 3.12 platform cells. Prepared CI is not executed evidence.

Live provider qualification is opt-in and requires HTTPS access. Run
`python tests/stac_live.py --binary BINARY --wheel WHEEL --out OUT --browser`
with that wheel installed and Playwright/Chromium available. It queries Earth
Search and Microsoft Planetary Computer by bbox/time, exercises pagination,
empty results and limits, verifies pinned Sentinel TIFFs, compares Python/HTTP
XYZ samples against independent GDAL reads, and switches dates in the regular
viewer. Planetary Computer checks an invalid-signature 403 followed by a fresh
signing callback. `tests/fixtures/stac-live.json` pins four original small TIFFs.
`--cache DIR` controls their ignored download cache. Signed URL mappings stay in
private report files and are excluded from public metadata and logs. These live
checks are separate from deterministic CI and do not establish provider uptime
or WAN throughput guarantees.

The field and operator contract is in [STAC guidance](../docs/how-to/stac.md).
Maintainer boundaries are in [STAC rationale](../design/stac.md).

`catalog_contracts.py` checks common keyed discovery, selected metadata, masked
arrays/Futures and decoded tiles across separate TIFFs, packed TIFFs, direct
STAC and database snapshots. It also checks stored expressions/defaults,
transaction rollback, key mappings and binding/cache invalidation against pinned
Sentinel crops and independent rasterio/NumPy references. Run it with the exact
installed wheel: `python tests/catalog_contracts.py --binary BINARY --wheel WHEEL
--out OUT`. `layer_contracts.py` retains the advanced mask, grid, overlap and
category qualification. The wheel workflow runs both suites in its CPython 3.12
platform cells and retains their reports. Prepared CI is not execution evidence.

`cloud_test_contracts.py --bundle BUNDLE --out OUT` checks disposable EC2
deployment preparation without credentials. It copies precomputed catalogs,
verifies S3 path/checksum preparation, extracts a complete native bundle, checks
loopback HTTP and browser viewport behavior against the independent demo PNG,
and exercises provisioning/collection failure handling. It needs Python 3.12+,
PyYAML, Pillow and Playwright/Chromium. Infrastructure schema validation uses
`cfn-lint packaging/cloud-test/storage.yaml packaging/cloud-test/template.yaml`.

`cloud_deployment.py --session SESSION --out OUT` runs against an existing cloud
host from an external client. `--url URL` also accepts another deployed endpoint.
Use `--workload JSON` for representative PNG viewport/pan/zoom/revisit requests
and optional checksum-pinned independent references. The default bundled crops
provide a smoke check. Add `--viewer-binary BINARY` to include Chromium navigation.
Reports distinguish decoding from independent image acceptance and omitted
browser checks. Operator procedures and spending/expiry behavior are in
[cloud test guidance](../docs/how-to/cloud-test.md).

## Diagnostics and first use

`diagnostic_contracts.py --binary INSTALLED_RCFP --out build/diagnostics` checks
configuration origins/redaction, pattern/destination failures, read-only catalogs,
physical band selection, local layout/masks/overviews, bounded render and HTTP
range probes, and supplied sample integrity. It requires installed RCFP, NumPy,
Pillow and Rasterio. `--browser` also requires Playwright/Chromium. Real raster
derivatives retain pinned source pixels, changing tiling and band count. An explicit Mercator affine isolates physical
band selection from projection differences. It does not qualify acquisition
geography.
The VRT text is a narrowly synthetic edge to verify external dependencies are
rejected before GDAL opens them. A tiny C library with a blocked initializer
checks the native child deadline on POSIX systems with a C compiler. Other
platforms record that case as skipped.

The runner exports imagery and executes the published first-map steps from a
fresh directory. Its PNG comparison uses the retained, independently acquired
Terracotta demo reference. Optional Chromium blocks external requests and checks
the offline map, RGB share restoration and repeated zoom at default server limits,
and the API-docs page. Each zoom waits for animation and successful decoding of
every tile. `first-use.json` records completion time, warnings/obstacles and
artifact identity. This automated trial does not claim a human usability study.
CPython 3.12 wheel cells run the contracts. Configured
CI is separate from completed local qualification.

## Indexed virtual mosaics

`mosaic_contracts.py --binary BINARY --out OUT` checks native footprint/attribute
selection, seams, first/last-valid ownership, mixed CRS/resolution, masks,
categories, dated selection and empty coverage with checksum-pinned real pixels.
Mercator/crop/decimation derivatives and repeated date labels isolate geometry
and selection. They do not establish acquisition history. Independent
Rasterio/NumPy calculations verify expressions before overlap and valid-mean or
center-nearest snapshot reduction. Preparation reproducibility, freshness,
category-average rejection, incomplete-product cleanup, RTree query plans and
index replacement/deletion have explicit checks.

The runner prepares a 100-file placement collection outside request timings,
then measures distinct four-request detailed and broad-product viewports on local
storage and a controlled 2 ms range origin. Reports retain source counts, planned
and actual reader invocations, source GET/HEAD/range counts and bytes, response
bytes, sampled service RSS, viewport completion and failures. Exhausted views
must submit no raster jobs or source requests. `--wheel WHEEL` also checks the
installed Python ownership/plan APIs. Linux native CI runs this suite and retains
its report. Prepared CI is not execution evidence. Cloud/WAN, physically cold
disk, browser paint and additional platforms remain separate qualification.

## Native map services

`native_services.py --binary BINARY --wheel WHEEL --out OUT --browser` checks
opaque tileset discovery, TileJSON, source points, selected-band calibration,
rotated affines, fractional alpha/nonfinite declarations, expressions, all four
named grids, dateline edges, cache reuse, schemas and Python/WSGI/Lambda. Run with
the exact wheel installed and NumPy, Pillow, Rasterio, jsonschema and optional
Playwright/Chromium. `fixtures/tile-grids/manifest.json` pins small native DEM
windows, original hashes/URLs and crop recipes. Arctic and southern stereographic
views use real source samples. The southern window is near 34°S. Browser grid
checks construct independent OpenLayers geographic/polar views from descriptors.
`browser_contracts.py` additionally checks the Leaflet/MapLibre/OpenLayers
TileJSON examples in native mode and unchanged keyed examples in compatibility
mode. Reports retain failures, skips and unavailable deployment/platform checks.

## Python-hosted compute layers

`python_compute_contracts.py --wheel WHEEL --out OUT --browser` runs against an
independently installed wheel. It checks explicit observations, named raw/scaled
binary inputs, independent per-band validity, mixed-resolution alignment, native
scalar rendering, output validation, version/source/parameter/dependency/style
identities, separate bounded caches, callback sharing, deadlines and shutdown.
Its threaded WSGI host demonstrates concurrent satellite-model requests with a
declared weather revision. Browser checks use the installed `rcfp connect` viewer
and the existing Leaflet, MapLibre and OpenLayers SDK examples.

Rasterio independently aligns the checksum-pinned Sentinel inputs. Calibration,
nodata declarations, decimation and value-preserving Float64 casts isolate numeric
and validity contracts. Callback algorithms are test model declarations. No RCFP
output becomes an oracle. The report records the exact wheel hash, Python runtime,
every case, metrics and separate skips/exclusions. Python 3.12 wheel CI cells run
the suite. Linux additionally prepares browser execution. Prepared CI is separate
from completed local platform qualification.
