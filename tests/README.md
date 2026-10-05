# Test suites and fixtures

Tests are organized by the interface they exercise. The Python end-to-end
runners are standalone scripts that write JSON reports; the installed-wheel
contract runner also writes JUnit XML. They are not collected by `pytest`.

Keep assertions, required case manifests, fixture provenance, and pinned oracle
expectations in this directory. Write generated outcomes to fresh ignored `build/`
directories; CI retains them as run artifacts. A saved outcome does not establish
that the current source or artifact passes. The native, wheel, correctness, and
documentation workflows are configured to rerun checks on pull requests and
pushes to `main`; expensive and environment-specific qualification is described
in the [validation handoff](../design/human-validation.md).

| Layer                         | Entry point                                                                                                                 | CI lane                                 | What it checks                                                                                                                                                                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rust unit tests               | `cargo test --locked`                                                                                                       | Native platforms                        | Core parser, expression, raster, settings, and storage logic on five OS/architecture cells.                                                                                       |
| Native profile corrections    | `python tests/native_contracts.py --binary BINARY --out OUT`                                                                | Native platforms                        | Both profiles: ingest validation/rollback, stale metadata, signed extrema, rotated validity hulls, percentile endpoints, empty dimensions, HTTP errors/specification, and modulo. |
| Rust SQL/remote integration   | `cargo test --locked --features storage-integration --test storage_sql --test storage_remote`                               | Correctness services                    | Disposable MySQL/PostgreSQL databases and remote storage with real services.                                                                                                      |
| Installed-wheel end-to-end    | `python tests/contract_suite.py --wheel WHEEL --out OUT`                                                                    | Wheels: 5 platforms × 3 Python versions | 53 required public Python, WSGI, CLI, HTTP, raster, metadata, and SQLite cases.                                                                                                   |
| Terracotta compatibility      | `RCFP_COMPAT_PROFILE=terracotta-0.10.1 python tests/compatibility_contracts.py --wheel WHEEL --out OUT`                     | Wheels: 5 platforms × 3 Python versions | Required legacy Python/CLI/WSGI and catalog contracts; Linux also replays against pinned Terracotta using `--reference-python`.                                                   |
| Other standalone end-to-end   | `tests/storage_contracts.py`, `remote_contracts.py`, `browser_contracts.py`, `platform_smoke.py`, `lambda_runtime_smoke.py` | Correctness, wheels, native platforms   | Live databases, HTTP ranges, Chromium UI, real raster, and official local Lambda runtime.                                                                                         |
| Large-file and resource tests | `tests/large_raster_contracts.py`, `resource_contracts.py`                                                                  | Scheduled, manual, or tag correctness   | Physical BigTIFF reads above 4 GiB and concurrent server resource behavior.                                                                                                       |
| Pinned reference comparison   | `tests/reference/differential.py`, `upstream.py`, `compatibility.py`                                                        | Correctness and wheels                  | 26 candidate comparisons, selected documented driver cases, and the pinned reference test baseline.                                                                               |

Reference setup, commands, and classified exclusions are documented in
[reference/README.md](reference/README.md).

The [broader raster matrix](reference/README.md#broader-raster-correctness)
adds pinned polar/dateline DEMs, rotated grids, dataset masks/RGBA, unusual CRSs,
overviews, source edges, and all 16 supported warp/read method pairs. It checks
both profiles through the freshly built shared core and writes per-case failures
without benchmarks. Its acquisition manifest is `fixtures/geography-sources.json`;
generated derivatives and results stay in ignored storage. The correctness
workflow runs the same general-reader matrix, with native direct reads explicitly
disabled, and retains its report separately.

Run `python tests/build_errors.py` with pip and the Hatch build dependencies
installed to check Linux build failure diagnostics through wheel and editable
metadata hooks. A deliberately failing Cargo executable checks that the original
error and exit status remain visible without an unrelated backend traceback.

The correctness services job publishes Rust source line coverage from its unit,
SQL/remote integration, and instrumented native catalog operations. The Linux
x86_64 / CPython 3.12 installed-wheel job publishes separate Python adapter line
and branch coverage from the in-process contract suite. GitHub job summaries show
the totals and lowest-covered files; downloadable artifacts contain Rust LCOV and
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
requires Docker and a built bundle and zip; a Python syntax pass is not a
runtime-image pass. The Rust expression vectors in `fixtures/` are retained
contract examples used by the unit suite. Rust storage tests create disposable
SQLite databases rather than depending on a preexisting database.

The optional `storage-integration` Cargo feature runs disposable engine tests.
Set `RCFP_TEST_MYSQL_URL` and `RCFP_TEST_POSTGRESQL_URL` to database server
URLs before enabling it; the tests create their own database names.

## Installed-wheel contract suite

`contract_suite.py` runs the public Python, WSGI, CLI, HTTP, raster, metadata,
and SQLite workflows against the installed wheel. It accepts the tested wheel
path with `--wheel` and writes JSON and JUnit reports with the wheel SHA-256.
The checked-in `fixtures/contract-cases.json` lists every required case ID;
removing or skipping a case fails the suite. NumPy supplies the compatibility
API’s masked-array return type. Pillow is a
test-only image decoder; Flask and Click are not runtime dependencies. The native
API can run without importing NumPy.

`compatibility_contracts.py` has its own required case manifest and artifact hash.
It verifies profile precedence and immutability, masked arrays and Futures,
named exceptions, lazy metadata and connection contexts, the WSGI entry point, every
CLI workflow, migration dry runs, and rejection of unsupported catalog formats.
Reference replay compares decoded images, metadata, types, and discovery in
both directions using catalogs created by each implementation. The existing
HTTP differential and live provider suites also run with the compatibility
profile selected. Only confirmed answer-changing optimizations are disabled;
Rust policy tests keep exact fast reads and prefetch available.

The live `compatibility_sql.py` test additionally needs SQLAlchemy, PyMySQL and
psycopg2-binary to administer disposable reference databases. Install those in
the test environment; they are not RCFP runtime dependencies.

`fixtures/corpus/` contains seven 512-pixel native windows of Sentinel-2,
Copernicus DEM, and ESA WorldCover source rasters. Their manifest records exact
parent URLs and hashes, pixel windows, TIFF hashes, pixel/mask hashes,
coordinate transforms, geographic bounds, and attribution. The crops retain
source values and masks; they were written as DEFLATE GeoTIFFs without warping,
resampling, or stretching. The suite verifies every fixture before it builds a
catalog. Keep the manifest and files together when changing the corpus.

`storage_contracts.py` drives the native executable through catalog operations
and live HTTP on disposable SQLite, MySQL, and PostgreSQL databases. It checks
transaction rollback and compares decoded images across backends. Set
`RCFP_TEST_MYSQL_URL` and `RCFP_TEST_POSTGRESQL_URL` for the SQL run; use
`--sqlite-only` for local feedback without server containers. Disposable
plaintext loopback servers need `?sslmode=disable` in each URL; remote servers
use verified TLS by default.

`native_contracts.py` checks deliberate native corrections against separate legacy
expectations, using the same binary and fresh per-profile catalogs. It verifies
the pinned corpus hashes, derives rotated opaque/masked windows from real red-band
pixels without resampling, and checks affine corners independently. Tiny generated
signed/constant rasters isolate extrema unavailable in the retained real corpus;
they are edge inputs, not replacement reference goldens. Reports identify the
tested binary hash and every profile/case result. The SQL suite additionally checks
native default MySQL singleband/RGB stretches, compatibility failures, and metadata
invalidation/lazy fill on every engine.

Both profiles also run a short eight-client render admission check with three
raster workers and a 64-job queue. The full 180-second resource suite remains a
separate scheduled/manual gate. Compatibility wheel contracts verify that SQL
credential/TLS URL changes obtain different drivers; live provider round trips
warm a connection, reject an incompatible TLS mode through another driver, and
check that the original connection still works.

`remote_contracts.py` compares local TIFF metadata and image pixels with the
same fixture served by a loopback HTTP server that records byte-range requests.
It runs against each installed platform wheel and fails when GDAL silently
reads the whole object instead of requesting ranges.

`browser_contracts.py` launches the bundled browser client and a native HTTP
backend, then uses Chromium to select a dataset, render singleband and RGB
layers, and open the metadata view. It records response statuses, page errors,
metadata, and screenshots. The test serves an offline map style so it does not
require a Mapbox token.

`large_raster_contracts.py` acquires the checksum-pinned full ESA WorldCover
source when needed, writes a physical tiled uint32 BigTIFF, checks all 5,041
source blocks and masks, and compares rendered tiles from the original, local
derivative, and a byte-range HTTP server. It requires an actual range request
above 4 GiB. `resource_contracts.py` repeatedly requests a real tile with
concurrent clients, checks image stability and error recovery, and bounds RSS
and file descriptor growth. These larger checks run on scheduled, manual, and
tag qualification; their reports are retained without uploading the multi-GB
fixture.

## Real map-navigation performance

`service_benchmark.py` compares native RCFP, Terracotta and TiTiler/rio-tiler on
the full checksum-pinned navigation inputs. Create separate CPython 3.12
environments with `benchmarks/terracotta.lock` and `benchmarks/titiler.lock`;
the latter includes the test-only image decoder. Configure the native GDAL
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
decode; requests are never retried out of the results. Service process-tree
PSS/USS samples include the Python masters/workers. Total logical cache budgets
include each worker's GDAL, array and curl caches; encoded-image retention is
off. Reports preserve every request, setting, source hash, latency and source
traffic count. Generated evidence stays in ignored `build/`.
The environment recorder hashes the actual installed application/raster code and
native Rasterio dependencies, including an editable Terracotta reference.

`benchmark_titiler_app.py` is a test-only SQLite-to-STAC adapter around TiTiler's
normal multi-asset factory/reader. It selects the same assets, 256-pixel PNGs,
compression level, numeric stretches and category palette; outside-source tiles
follow the common transparent-image policy. It uses rio-tiler's stretch to the
1–255 valid-value range; integer truncation remains rio-tiler's own behavior.
The saved-image quality runner checks independent GDAL output grids, dimensions,
coverage and category colors and reports sampling/rounding differences. It does
not treat any service output as a golden or relabel differences as passes.
These comparisons leave cold disk, browser paint, actual S3, genuine temporal
acquisitions, dynamic mosaics and deadline/disconnect qualification separate.

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
use four cores/workers and four parallel requests, GDAL 64 MiB and array 64 MiB;
`--setting NAME=VALUE` overrides controls explicitly. `--storage s3-public` uses
actual public URLs; range mode injects 15 ms per source request and is a surrogate.
A timeout/non-200 fails the run and remains in its report. Physically cold disk,
WAN/browser paint and private credential refresh require separate environments.

For three sequential alternating paired trials against a frozen baseline:

```sh
python tests/navigation_compare.py --baseline build/baseline-rcfp --candidate target/release/rcfp --out build/navigation-paired --allow-image-change --candidate-setting TC_REMOTE_SOURCE_CACHE_TTL_SECONDS=60 --candidate-setting TC_RENDER_CACHE_BYTES=16777216 --candidate-setting TC_REMOTE_RENDER_CACHE_TTL_SECONDS=60 --candidate-setting TC_RASTER_CACHE_BYTES=50331648 -- --storage local range --users 1 3
```

This divides the same 128 MiB aggregate logical GDAL/application budget between
arrays/images. Do not run other timing experiments on its pinned cores. Preserve
individual cells and failures; `--allow-image-change` requires independent native
quality qualification and does not turn the candidate into its own oracle.
Image hashes establish equality across exact optimizations, not geographic truth.

`navigation_prepare.py --binary ... --out build/map-derivatives --block-size 1024`
prepares full map-grid derivatives and a separate bundled-RGB manifest, retaining
original navigation bounds and build-time/storage costs. Feed its
`manifest.json`/`multiband-manifest.json` to paired comparisons with
`--candidate-manifest`. Fixture preparation never writes reference goldens.
Smaller blocks and bundled RGB can regress remote/singleband workloads.
`--candidate-rgb-format webp --allow-image-change` compares RGB display codecs;
paired checks additionally require exact alpha and dimensions. Native PNG versus
WebP timing/bytes are separate from `display_encoding_benchmark.py`, a Pillow
feasibility probe on real captured tile inputs.

`native_rendering.py --binary ... --out build/native-grid` checks tiles against
independent public GDAL warp/Rasterio reads. It requires navigation originals
plus the pinned sources in `fixtures/geography-sources.json`, stored under
`.cache/raster-geography/`. Reproduce nearest, cubic and average with
`--resampling`; default is bilinear. The full matrix checks exact affine grids,
coverage, category colors, continuous channels within one byte, polar/dateline,
rotated real source pixels, unaligned Mercator, southern UTM, overviews, rectangular dimensions,
multiband RGB and WebP coverage. A real-value DEM with a synthetic fractional
alpha edge isolates floating alpha semantics. Reports identify every check and
binary hash. The crop/derivative recipes are test inputs, not Terracotta goldens.

`navigation_cache_contracts.py --binary ... --out build/cache-freshness` verifies
cache hits bypass raster execution, dimension keys, external SQLite
metadata/deletion, raster writes, and external mask creation/replacement. Unit
checks cover TTL/eviction/coalescing/failure cleanup and queued cancellation.
Run `resource_contracts.py` with `TC_NATIVE_DIRECT_READ=true` to exercise sustained
new-path reads; image retention stays off in that execution-accounting test.
These focused native checks do not close the broader existing metadata/preview
boundary matrix or qualify every remote provider/platform.
