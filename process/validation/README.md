# P00 reference and comparison workflow

All commands run from the repository root. Network use is explicit: bootstrap
installs pinned packages and acquisition downloads pinned public rasters. Routine
checks are offline. Generated inputs and run outputs live in `.cache/corpus` and
`process/validation/artifacts`; both are intentionally ignored. The retained golden
capture, manifests, comparator tests, and P00 evidence are versioned.

## Build the frozen oracle

Download the frozen Terracotta 0.10.1 source release and build its pinned
reference environment:

```bash
process/validation/tools/bootstrap-reference.sh
```

The source archive is fetched from PyPI, verified against SHA-256
`e5d2ac9b78a36d0f28414b1a4890c961dede50d1f9ce33b8d23eee643f5e2348`,
and extracted to ignored `.cache/reference`. The PyPI release attests upstream
commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`. Set
`TERRACOTTA_SOURCE_ARCHIVE=/path/to/terracotta-0.10.1.tar.gz` to use a cached
copy. The bootstrap refuses to replace an existing snapshot or environment.
`process/validation/reference/environment.json` records the pinned Python, package,
GDAL, PROJ, GEOS, SQLite, codec, native-library and data-file versions and
hashes from the original P00 run. The downloaded sdist includes the tests,
fixtures, browser assets and `apidoc.html` needed by this harness. Its only
source difference from the frozen Git snapshot is release generated
`terracotta/_version.py`; the package and test files otherwise compare exactly.

Rust 1.85.1 is pinned in `rust-toolchain.toml`. A normal Rust installation can
run `cargo build --locked`. The helper also recognizes the workspace-local
toolchain used for the P00 evidence:

```bash
packaging/cargo.sh build --locked --offline
packaging/cargo.sh run --locked --offline -- --version
```

## Acquire and derive the corpus

The six parent assets total 501,570,928 bytes. Allow at least 758 MiB free: the
download plus a 256 MiB safety reserve. Failed downloads remain unpublished as
`.part`; retries start the bounded transfer again. Exact URLs, byte lengths,
ETags, SHA-256 values, terms, attribution, and selection purpose are in
`reference/assets.json`.

```bash
.venv-reference/bin/python process/validation/tools/acquire.py --max-bytes 629145600
.venv-reference/bin/python process/validation/tools/subset.py
```

Subsets are native pixel windows. They are not warped, resampled, stretched, or
dtype-converted. `reference/subsets.json` gives every pixel window and identifies
the one derived internal-mask variant. The subset command compares pixels,
transform, CRS, dtype, scale/offset, and mask with each parent window. Ordinary
subset TIFF organization differs from the COG parents and is not storage-format
evidence. Recheck cached inputs without network access with:

```bash
.venv-reference/bin/python process/validation/tools/acquire.py --offline
.venv-reference/bin/python process/validation/tools/subset.py --verify
```

## Capture and compare

Capture refuses to overwrite an output directory. It creates a Python SQLite
database, captures HTTP/JSON/errors, decoded arrays and masks, PNGs, adjacent
tiles, compute/RGB/categorical views, storage rows, and small timing context.

```bash
.venv-reference/bin/python process/validation/tools/capture.py --out process/validation/artifacts/capture-candidate
.venv-reference/bin/python process/validation/tools/compare.py \
  process/validation/reference/capture process/validation/artifacts/capture-candidate
```

The comparator checks HTTP structure and order, status, selected headers,
database values including blobs, image dimensions, alpha/masks, visible pixels,
and typed array values. PNG compression bytes are deliberately ignored. Its 19
tests prove detection of changed status, missing fields, key/list order, storage
values, types, artifact membership, dimensions, alpha, pixels, shifts, seams,
categories, missing/corrupt images, array values, and array masks.

The visual index is `reference/capture/index.html`; its compact contact sheet is
`reference/capture/contact-sheet.png`. Each view links exact subset, location,
XYZ coordinates where relevant, and settings through `views.json`.

## Stress and routine checks

The stress command reads the unaltered 36,000 × 36,000 WorldCover COG under a
512 MiB RSS watchdog, once as a broad preview and once at z15 against native
blocks. It never substitutes a downsampled file.

```bash
.venv-reference/bin/python process/validation/tools/stress.py \
  --out process/validation/artifacts/stress-candidate --budget-mib 512
process/validation/tools/check.sh
```

The 85,040,674-byte classic TIFF represents 1.296 GB of decoded values, 2.592 GB
with a byte mask, or 5.184 GB as a float32 working array. This establishes a
decoded-memory case. It does not establish physical multi-GiB or BigTIFF-offset
coverage; that remains open for P02.

## Build and validate the P01 Rust slice

The locked dependencies are cached after the explicit initial `cargo fetch`.
Routine builds and tests are offline:

```bash
packaging/cargo.sh build --locked --offline
packaging/cargo.sh test --locked --offline
packaging/cargo.sh clippy --locked --offline --all-targets -- -D warnings
```

Run the native server against the unchanged Python-created database:

```bash
packaging/cargo.sh run --locked --offline -- \
  serve -d process/validation/artifacts/capture-reproduction/reference.sqlite \
  --bind 127.0.0.1:5000
```

In this P00 workspace the executable discovers Rasterio's pinned GDAL 3.9.3
runtime and re-execs itself with that private library directory. A separately
installed GDAL can instead be selected with `TERRACOTTA_GDAL_LIBRARY`; packaged
native-library discovery remains P03 work.

The fast validator starts the server, exercises 26 retained API/image/error
cases, compares exact status, selected headers, JSON values and decoded RGBA,
and writes a six-pair native-scale inspection sheet:

```bash
.venv-reference/bin/python process/validation/tools/p01_validate.py \
  --out process/validation/artifacts/p01-fast
```

The stress validator redirects a generated copy of the Python database to the
checksum-pinned 36,000 × 36,000 publisher COG, compares broad-preview and z15
base-resolution results to Python, and enforces a 512 MiB server RSS budget:

```bash
.venv-reference/bin/python process/validation/tools/p01_stress.py \
  --out process/validation/artifacts/p01-stress --budget-mib 512
```

Both commands require fresh output directories. Retained final results and the
inspection sheet are under `process/validation/evidence/p01/`.

## Build and validate the P02 bounded RGB/remote slice

P02 keeps routine Rust and corpus checks offline, and uses loopback-only HTTP
servers for deterministic byte-range evidence. Each runner requires a fresh
output directory:

```bash
.venv-reference/bin/python process/validation/tools/p02_validate.py \
  --out process/validation/artifacts/p02-fast --port 5094 --range-port 5194
.venv-reference/bin/python process/validation/tools/p02_failure.py \
  --out process/validation/artifacts/p02-failure --port 5095 --range-port 5195
.venv-reference/bin/python process/validation/tools/p02_stress.py \
  --out process/validation/artifacts/p02-stress --port 5096 --range-port 5196 \
  --budget-mib 512
```

The service defaults to three raster workers, eight queued jobs, a 30-second
response wait, 64 MiB raw-raster cache, and 60-second remote cache TTL. Override
these with `TC_RASTER_WORKERS`, `TC_RASTER_QUEUE_CAPACITY`,
`TC_RASTER_TIMEOUT_MS`, `TC_RASTER_CACHE_BYTES`, and
`TC_REMOTE_RASTER_CACHE_TTL_SECONDS`. Raster S3 configuration accepts
`TC_RASTER_AWS_ACCESS_KEY`, `TC_RASTER_AWS_SECRET_KEY`,
`TC_RASTER_AWS_SESSION_TOKEN`, `TC_RASTER_AWS_S3_ENDPOINT`, and
`TC_RASTER_REMOTE_TIMEOUT_SECONDS`; values are mapped to GDAL at startup and are
never emitted by the metrics endpoint.

`p02_validate.py` compares eight RGB cases exactly after RGBA decoding and
records local/range traffic, cache behavior, concurrency, memory, and handles.
`p02_failure.py` verifies 503/504 behavior, continued accounting after response
timeout, and failure recovery. `p02_stress.py` sends the checksum-pinned full
WorldCover parent through local and byte-range paths for overview and
base-resolution reads. Retained results are under `process/validation/evidence/p02/`.

## Build and validate the P03 distribution slice

Every output path below must be fresh. The host bundle vendors the complete
Rasterio GDAL/PROJ/codec directory and data files, retains required notices, and
does not discover `.venv-reference` when run:

```bash
packaging/build-bundle.sh process/validation/artifacts/p03-bundle
packaging/build-python.sh \
  process/validation/artifacts/p03-bundle process/validation/artifacts/p03-python
```

Build the Lambda executable inside the pinned official `provided.al2023` image,
then substitute that target-specific binary and base libraries into the zip:

```bash
process/validation/tools/build-lambda-target.sh process/validation/artifacts/p03-lambda-target
RCFP_LAMBDA_TARGET=process/validation/artifacts/p03-lambda-target \
  packaging/build-lambda.sh \
  process/validation/artifacts/p03-bundle process/validation/artifacts/p03-lambda
```

Run the local extension/WSGI/event/runtime and cross-language database suite,
the clean container smoke, and the official-runtime-image smoke:

```bash
.venv-reference/bin/python process/validation/tools/p03_validate.py \
  --out process/validation/artifacts/p03-local
.venv-reference/bin/python process/validation/tools/p03_container.py \
  --out process/validation/artifacts/p03-container
.venv-reference/bin/python process/validation/tools/p03_lambda_runtime.py \
  --out process/validation/artifacts/p03-lambda-runtime
```

The last two commands require Docker; building the images/target may require
network access. Runtime render checks disable container networking. Retained
results, exact artifact identities, platform coverage, and the intentionally
pending real AWS invocation are in `process/validation/evidence/p03/README.md`.

## Validate the P04 rendering and HTTP contracts

The frozen environment, offline corpus/subsets and
`process/validation/artifacts/capture-reproduction/reference.sqlite` from P00 are
prerequisites. Use fresh output paths; the HTTP and edge tests bind disposable
loopback services. Build the debug executable before those tests:

```bash
packaging/cargo.sh build --locked --offline
.venv-reference/bin/python process/validation/tools/p04_assets.py
.venv-reference/bin/python process/validation/tools/p04_expressions.py
process/validation/tools/check.sh
packaging/cargo.sh fmt -- --check
packaging/cargo.sh clippy --locked --offline --all-targets -- -D warnings
.venv-reference/bin/python process/validation/tools/p04_validate.py \
  --out process/validation/artifacts/p04-http-release
.venv-reference/bin/python process/validation/tools/p04_edges.py \
  --out process/validation/artifacts/p04-edges-final
packaging/build-bundle.sh process/validation/artifacts/p04-release
packaging/build-python.sh \
  process/validation/artifacts/p04-release process/validation/artifacts/p04-python-release
.venv-reference/bin/python process/validation/tools/p04_adapters.py \
  --bundle process/validation/artifacts/p04-release \
  --package process/validation/artifacts/p04-python-release \
  --out process/validation/artifacts/p04-adapters-release
```

`p04_expressions.py` regenerates vectors from the Python oracle; it never reads
Rust outputs. `P04_FOCUS=1` selects a quick HTTP subset and is not the full palette
qualification. `p04_adapters.py` verifies the packaged Python/WSGI and local
Lambda event request path, including metadata bodies, headers and concurrent
pool lifecycle; it does not deploy a Lambda or replace target-image testing.

The [P04 evidence](evidence/p04/README.md) contains actual counts, the full
route/option matrix, source/artifact hashes, exact reproduction commands,
retained image pairs and the narrow RW-019 float32 acceptance decision. The
existing P01/P02 scripts provide the singleband, RGB, remote-range and concurrency
regressions. Complete legacy Python APIs, settings, CLI and browser work remain
assigned to their later phases.

## P05 metadata, ingestion and optimization

All commands below reuse the frozen source/environment and local P00 corpus.
No tests silently download data. Use fresh package output directories; HTTP
checks require loopback binding permission.

```sh
packaging/cargo.sh test --locked --offline
packaging/cargo.sh clippy --all-targets -- -D warnings
packaging/build-bundle.sh process/validation/artifacts/p05-qualified
packaging/build-python.sh process/validation/artifacts/p05-qualified process/validation/artifacts/p05-qualified-python
.venv-reference/bin/python process/validation/tools/p05_validate.py \
  --binary process/validation/artifacts/p05-qualified/bin/terracotta \
  --output process/validation/artifacts/p05-qualified-checks
.venv-reference/bin/python process/validation/tools/p05_edges.py
.venv-reference/bin/python process/validation/tools/p05_metadata_images.py
.venv-reference/bin/python process/validation/tools/p05_quirks.py
.venv-reference/bin/python process/validation/tools/p05_workflows.py
.venv-reference/bin/python process/validation/tools/p04_adapters.py \
  --bundle process/validation/artifacts/p05-qualified \
  --package process/validation/artifacts/p05-qualified-python \
  --out process/validation/artifacts/p05-qualified-adapters
TC_P05_STRESS_OUT="$PWD/validation/artifacts/p05-stress-final" \
  .venv-reference/bin/python process/validation/tools/p05_stress.py
```

`p05_validate.py --section metadata|database|optimization|cli` selects a focused
suite. Edge/image runners use `target/release/terracotta`; package workflow paths
can be overridden by `TC_P05_BUNDLE`/`TC_P05_PACKAGE`. The stress runner checks the
original 36,000×36,000 WorldCover hash, computes chunked statistics in both
languages, optimizes the entire file through disk intermediates, compares every
base/mask/overview block, and measures separate process RSS under 512 MiB. It
writes large TIFFs to ignored artifact storage and can take several minutes.

Native user workflows:

```sh
process/validation/artifacts/p05-qualified/bin/terracotta optimize-rasters '.cache/corpus/subsets/*.tif' -o prepared --compression auto --nproc 2
process/validation/artifacts/p05-qualified/bin/terracotta ingest 'prepared/lagoon-{band}.tif' -o prepared.sqlite --skip-metadata
process/validation/artifacts/p05-qualified/bin/terracotta serve -d prepared.sqlite --port 5000
# Or directly explore a pattern, with metadata computed on first request:
process/validation/artifacts/p05-qualified/bin/terracotta serve -r 'prepared/lagoon-{band}.tif'
```

Ingest overwrites existing keys unless `--skip-existing`; `--skip-metadata`
retains existing metadata on overwrite, matching the reference. Optimize requires
`--overwrite` or `--skip-existing` for existing outputs. `--in-memory` and
`--no-in-memory` force intermediate storage; the default uses the reference
10980²-pixel threshold. All four reference resamplers and compression choices
are supported. Source masks and first-band behavior follow the reference.

`rcfp.compute_metadata` and `rcfp.DataStore` expose P05
preparation through the in-process core; `DataStore.batch` is atomic. The
`data-operation REQUEST.json` native diagnostic adapter supports the same
create/insert/delete/metadata/compute/optimize/batch operations for reproducible
validation. Full legacy driver contexts, exceptions, ndarray representation,
settings and import compatibility remain P07. See [P05 evidence](evidence/p05/README.md)
for exact contracts, accepted RW-024/RW-025 bounds, and retained views.

## P06 metadata backends and migrations

Start disposable, loopback-only engines (Docker access required), wait for
`status` to succeed, then configure their short-lived TLS fixture:

```sh
process/validation/tools/p06-services.sh start
process/validation/tools/p06-services.sh status
process/validation/tools/p06-services.sh tls
packaging/cargo.sh build --locked --offline
.venv-reference/bin/python process/validation/tools/p06_validate.py
.venv-reference/bin/python process/validation/tools/p06_paths.py
.venv-reference/bin/python process/validation/tools/p06_migrations.py
.venv-reference/bin/python process/validation/tools/p06_tls.py
.venv-reference/bin/python process/validation/tools/p06_remote.py
packaging/cargo.sh test --locked --offline --features storage-integration
```

The SQL URLs and `p06_*` database names are disposable test inputs, never
production defaults. `p06_validate.py` accepts `TC_P06_BINARY` to qualify a
packaged executable. The Rust `storage-integration` feature explicitly enables
socket/engine tests; normal `cargo test` runs the offline unit tests. No skipped
integration target is counted as a pass. S3 emulator/signing/lease tests use fake
credentials and require no AWS account. Reports go to ignored `p06*` artifacts;
retained results and exact commands are indexed in [P06 evidence](evidence/p06/README.md).

Build the portable Linux x86_64 package against the established older ABI:

```sh
process/validation/tools/build-lambda-target.sh process/validation/artifacts/p06-native-target
RCFP_NATIVE_TARGET="$PWD/process/validation/artifacts/p06-native-target" \
  packaging/build-bundle.sh process/validation/artifacts/p06-release
packaging/build-python.sh process/validation/artifacts/p06-release process/validation/artifacts/p06-release-python
packaging/build-lambda.sh process/validation/artifacts/p06-release process/validation/artifacts/p06-release-lambda
```

Host builds remain supported for development; their libc ABI depends on the
host. Do not use the new host build on older glibc targets (RW-031). Bundle builds
retain Rust dependency licenses, the vendored OpenSSL license and GDAL notices.
Existing Debian/official Lambda-image runners accept the portable bundle/zip.
No AWS deployment is implied. Finish by removing only the disposable test engines:

```sh
process/validation/tools/p06-services.sh stop
```

Native service/ingestion auto-detect `mysql://`, `postgresql://` and `s3://`;
local and `sqlite:///` paths select SQLite. `serve --database-provider` and
`TC_DRIVER_PROVIDER` allow explicit selection. `TC_SQL_USER`/`TC_SQL_PASSWORD`
provide SQL credential fallback and `TC_DB_CONNECTION_TIMEOUT` controls connection
establishment (default 10 seconds). PostgreSQL supports `sslmode`, `sslrootcert`,
`application_name`, `options`, and `connect_timeout`. The frozen MySQL driver's
malformed URL-query suffix remains an explicit error (RW-030).

S3 SQLite is read-only, uses standard AWS environment/shared-profile/role
credentials, and supports `AWS_ENDPOINT_URL_S3` or `AWS_ENDPOINT_URL` for a custom
endpoint. `TC_REMOTE_DB_CACHE_TTL` defaults to 600 seconds and
`TC_REMOTE_DB_CACHE_DIR` to the system temporary directory's `terracotta` folder.
A live store retains its cached generation; refresh validates a complete new
snapshot before publishing it. Failed refresh returns an error and retries on
the next request. Prior readers retain their old files until their connections
close. Raster `TC_RASTER_AWS_*` settings remain separate.

`terracotta migrate DATABASE [--from VERSION] [--to VERSION] [-y]` supports the
frozen 0.7→0.8→0.9→0.10 chain and defaults to protocol 0.10.1, ignoring patch
versions for migration selection. Run it on a disposable copy first and retain
the original for rollback. MySQL DDL commits implicitly: restore the original
copy after a failed chain before retrying. Its 0.7 migration is already invalid
in the reference; that error and unchanged version marker are preserved.

Reopened MySQL metadata has the legacy ten-decimal HTTP conversion. Default
singleband/RGB stretches also retain the reference's Decimal/NumPy failure;
explicit complete numeric or percentile ranges work (RW-028). These quirks are
recorded for later correction, not silently changed in this port.

## P07 integrated workflows and platform CI

The legacy import/CLI, persistent Flask/WSGI applications, native settings,
contexts/futures, unchanged browser and distribution are implemented. Use the
[P07 evidence index](evidence/p07/README.md) for exact commands and classified
failures; [operator instructions](../../packaging/OPERATIONS.md) cover installation,
configuration, profiles and resource ownership. Final local artifacts are
`p07-final`, `p07-final-python`, `p07-final-wheels`, and `p07-final-lambda`.

[Platform qualification CI](../../.github/workflows/platforms.yml) is prepared and
linted; its Linux staging and Lambda Docker build were tested locally. The user
deferred macOS/Windows/arm64 runs until later, so those results remain pending.
Actual AWS Lambda/S3/X-Ray services and broader release qualification remain P08.

## P08 combined release qualification

[P08 evidence](evidence/p08/README.md), [performance conditions/results](evidence/p08/PERFORMANCE.md),
[candidate notes](../RELEASE_CANDIDATE.md) and [rollback](../ROLLBACK.md)
describe the qualified local candidate and remaining release gates. Use the P08
final packages: P07's zip has a reproduced intermittent shutdown defect (RW-039).

The offline P00 corpus remains required. Additional polar/dateline assets are
pinned in `evidence/p08/geography-assets.json`. Acquisition is explicit:

```sh
.venv-reference/bin/python process/validation/tools/p08_acquire.py --download
.venv-reference/bin/python process/validation/tools/p08_acquire.py
```

Build final artifacts against the established portable ABI. A reusable target
cache can replace NEW-target only when `TERRACOTTA_REUSE_TARGET=1` is specified:

```sh
process/validation/tools/build-lambda-target.sh process/validation/artifacts/NEW-target
RCFP_NATIVE_TARGET=process/validation/artifacts/NEW-target packaging/build-bundle.sh process/validation/artifacts/p08-final
packaging/build-python.sh process/validation/artifacts/p08-final process/validation/artifacts/p08-final-python
packaging/build-wheel.sh process/validation/artifacts/p08-final process/validation/artifacts/p08-final-wheels
packaging/build-lambda.sh process/validation/artifacts/p08-final process/validation/artifacts/p08-final-lambda
```

Use fresh output directories or retain old reports before a new qualification.
The benchmark requires Gunicorn 23.0.0 in its isolated tool directory. It compares
three sync production workers with three native raster workers on identical CPU
affinity, local/loopback-range inputs, cache-off/cache-on and concurrency 1/3.
Run it separately from heavy validation jobs; raw samples and exact RGBA checks
are retained, with no WAN or cold-disk claim:

```sh
.venv-reference/bin/python -m pip install --target process/validation/artifacts/p08-benchmark-tools gunicorn==23.0.0
.venv-reference/bin/python process/validation/tools/p08_benchmark.py
.venv-reference/bin/python process/validation/tools/p08_performance_report.py
process/validation/tools/p06-services.sh start
process/validation/tools/p06-services.sh status
.venv-reference/bin/python process/validation/tools/p08_qualify.py
process/validation/tools/p06-services.sh stop
```

The combined runner records every exit code in `evidence/p08/qualification.json`.
It explicitly classifies the two unchanged oracle ANSI failures; no other failed,
skipped or excluded test is silently accepted. `--resume` skips only completed
accepted job records; investigate failed output directories before retrying.
The runner includes real SQL engines, the complete applicable upstream suite,
HTTP/adapters, settings/public remote emulator, preparation/migrations/rollback,
polar/dateline images and full/sample/chunk metadata, actual >4 GiB TIFF offsets, application destruction/TCP
disconnect, overload/recovery, a 180-second mixed soak, 200 cold native exits in
the official AL2023 image, and final clean-container/runtime smokes.

The BigTIFF fixture occupies about 7.1 GB in ignored artifact storage. Its recipe
widens every original WorldCover uint8 sample exactly to uint32, writes actual
uncompressed blocks and builds nearest overviews. All base pixels/masks are
verified; physical allocation and actual HTTP range offsets must exceed 4 GiB.
This is derived storage evidence, not a publisher-native BigTIFF.

Browser qualification uses the final package, with a test-only offline basemap
and a 64-job queue for the client's tile burst; every backend response must succeed:

```sh
PYTHONPATH=process/validation/artifacts/p07-browser-tools PLAYWRIGHT_BROWSERS_PATH=process/validation/artifacts/p07-browsers \
  TC_BROWSER_BINARY=process/validation/artifacts/p08-final/bin/terracotta TC_BROWSER_QUEUE=64 TC_BROWSER_OUT=process/validation/evidence/p08/browser-qualified \
  .venv-reference/bin/python process/validation/tools/p07_browser.py
```

Install the final wheel in a new environment and run `p07_platform_smoke.py` with
its Python interpreter and `--binary process/validation/artifacts/p08-final/bin/terracotta`.
`p08_reconcile.py` retains a per-record inventory audit, including actual related
upstream outcomes and explicit external blockers. Prepare deterministic local
archives only after evidence/documentation are finalized:

```sh
.venv-reference/bin/python process/tools/prepare-candidate.py --out process/validation/artifacts/p08-candidate
.venv-reference/bin/python process/tools/prepare-candidate.py --out process/validation/artifacts/p08-candidate-repeat
```

`p08_archive_smoke.py` checks canonical archives in `p08-candidate-preflight`,
including wheel RECORD integrity, offline installation into `p08-final-install`,
and native/WSGI/official-Lambda-image rendering. Use `p08_collect.py` to retain
compact reports and final source/runtime hashes, then `p08_reconcile.py` to audit
the inventory. Retain initial failed metadata evidence when rerunning collection.

Compare both SHA256SUMS files. Archive reproducibility is distinct from compiler
bit reproducibility. No command here pushes, tags, publishes or deploys; deferred
platforms and actual AWS service checks remain explicit release gates.
