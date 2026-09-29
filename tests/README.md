# Test suites and fixtures

Tests are organized by the interface they exercise. The Python end-to-end
runners are standalone scripts that write JSON reports; the installed-wheel
contract runner also writes JUnit XML. They are not collected by `pytest`.

| Layer | Entry point | CI lane | What it checks |
| --- | --- | --- | --- |
| Rust unit tests | `cargo test --locked` | Native platforms | Core parser, expression, raster, settings, and storage logic on five OS/architecture cells. |
| Rust SQL/remote integration | `cargo test --locked --features storage-integration --test storage_sql --test storage_remote` | Correctness services | Disposable MySQL/PostgreSQL databases and remote storage with real services. |
| Installed-wheel end-to-end | `python tests/contract_suite.py --wheel WHEEL --out OUT` | Wheels: 5 platforms × 3 Python versions | 53 required public Python, WSGI, CLI, HTTP, raster, metadata, and SQLite cases. |
| Other standalone end-to-end | `tests/storage_contracts.py`, `remote_contracts.py`, `browser_contracts.py`, `platform_smoke.py`, `lambda_runtime_smoke.py` | Correctness, wheels, native platforms | Live databases, HTTP ranges, Chromium UI, real raster, and official local Lambda runtime. |
| Large-file and resource tests | `tests/large_raster_contracts.py`, `resource_contracts.py` | Scheduled, manual, or tag correctness | Physical BigTIFF reads above 4 GiB and concurrent server resource behavior. |
| Transitional reference comparison | `process/validation/tools/ci_differential.py`, `ci_upstream.py` | Correctness | 26 candidate comparisons against pinned Terracotta and the pinned reference test baseline. |

The correctness services job publishes Rust source line coverage from its unit,
SQL/remote integration, and instrumented native catalog operations. The Linux
x86_64 / CPython 3.12 installed-wheel job publishes separate Python adapter line
and branch coverage from the in-process contract suite. GitHub job summaries show
the totals and lowest-covered files; downloadable artifacts contain Rust LCOV and
Python JSON/XML. These numbers exclude wheel native extension, browser, Lambda, and other
platform runs. Terminated HTTP server subprocesses may not flush Rust counters.
Coverage is reported as a baseline, with no percentage gate until hosted
reports establish a stable value.

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
removing or skipping a case fails the suite. NumPy and Pillow are test tools,
not runtime dependencies of the package.

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
`--sqlite-only` for local feedback without server containers.

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
