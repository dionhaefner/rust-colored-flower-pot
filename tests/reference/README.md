# Pinned Terracotta reference checks

These runners are maintained compatibility gates for every release shipping the
`terracotta` profile. They complement the standalone RCFP assertions and
run independently of developer documentation.

## Prepare the reference

Run commands from the repository root, using CPython 3.12:

```sh
tests/reference/bootstrap.sh
```

Bootstrap downloads the Terracotta 0.10.1 PyPI source archive, verifies SHA-256
`e5d2ac9b78a36d0f28414b1a4890c961dede50d1f9ce33b8d23eee643f5e2348`,
and extracts it into ignored `.cache/reference`. Its published source commit is
`6db38d5873cde5ebabe47338a1f61ddc788eada6`. The isolated `.venv-reference`
environment uses `requirements.lock`. Bootstrap refuses to replace either
existing directory. Keep application source unchanged. Do not vendor it into RCFP.

For offline source acquisition, set `TERRACOTTA_SOURCE_ARCHIVE` to a cached copy
of that exact sdist. Installing dependencies still requires package access or a
populated pip cache. `fetch.py --archive ARCHIVE --output DIRECTORY` can verify
and extract the source without installing dependencies.

Bootstrap writes package/native versions, source and native-file hashes, and a
resolved dependency listing under `.cache/reference`. It never regenerates the
checked-in dependency lock. Change that lock deliberately with baseline evidence.

The demo preview reference uses the supplied 512-pixel Sentinel red crop rather
than the larger platform-smoke crop. Acquire it independently with
`.venv-reference/bin/python tests/reference/acquire_demo.py --out build/demo-reference`.
Compare its pinned manifest with `tests/fixtures/demo/manifest.json` before
reviewing any fixture update. RCFP output is never a reference source.

## Run the gates

Build `target/release/rcfp` and configure its GDAL runtime before HTTP checks.
CI locates the conda runtime with `tests/ci-gdal-env.py`. Locally, use a
configured GDAL library or replace `--binary target/release/rcfp` below with
`--binary /path/to/bundle/bin/rcfp` from a complete native bundle. Use a fresh
output directory for every run:

```sh
cargo build --locked --release
TC_NATIVE_DIRECT_READ=false python tests/reference/differential.py --binary target/release/rcfp --reference-python .venv-reference/bin/python --out build/reference-http
RCFP_COMPAT_PROFILE=terracotta TC_RUST_MERCATOR_FAST=1 TC_PREFETCH_TILE_RING=1 TC_RUST_UTM_FAST=1 python tests/reference/differential.py --binary target/release/rcfp --reference-python .venv-reference/bin/python --out build/reference-http-compatibility
python tests/reference/compatibility.py --candidate-python /absolute/path/to/wheel-env/bin/python --reference-python .venv-reference/bin/python --out build/reference-public
```

`differential.py` verifies the seven checksum-pinned `tests/fixtures/corpus`
rasters, creates a catalog, and checks 26 HTTP/status/JSON/decoded-image cases.
It accepts the RCFP specification title, native POST metadata/band/format
specification additions, and classified error meaning differences. It requires
exact decoded images. Run both native general-reader controls and compatibility
profiles. Default native direct-grid sampling has an independent quality gate in
`tests/native_rendering.py` and can differ from Terracotta's intermediate grid.

`compatibility.py` replays an explicit selection of documented driver workflows
through the installed wheel: creation, discovery/filtering, lazy/precomputed
metadata, writes/deletion, raster reads and meaningful errors. It adapts imports
in an output copy. The reference snapshot remains untouched. SQL providers have
separate bidirectional live-engine checks. Internal helper, private backend-hook,
Flask and Click tests are not public Python API gates. A missing, skipped or
failed selected case fails the gate.

The reference's own baseline needs disposable MySQL and PostgreSQL services:

```sh
python tests/reference/upstream.py --reference-python .venv-reference/bin/python --mysql-server root:rcfp-ci@127.0.0.1:3306 --postgresql-server postgres:rcfp-ci@127.0.0.1:5432 --out build/reference-baseline
```

This expects 526 passes and two known ANSI logging capture failures out of 528
cases, with six remote-SQLite mock cases explicitly excluded. Additional failures,
errors, skips, or a changed case count fail the baseline. These checks establish
the reference environment. Their passes are not RCFP product passes. Native S3
behavior is checked through socket/service tests rather than Python boto mocks.

Installed-wheel replay and live engine round trips also run through
`tests/compatibility_contracts.py --reference-python ...` and
`tests/compatibility_sql.py --reference-python ...`. The correctness and wheel
workflows retain machine-readable reports and artifact identities.

## Broader raster correctness

Run the boundary matrix against the freshly built shared core, using the prepared
reference environment for NumPy, Rasterio, Pillow, and Shapely:

```sh
cargo build --locked --release
.venv-reference/bin/python tests/reference/raster_boundaries.py --library target/release/librcfp_core.so --reference-python .venv-reference/bin/python --out build/raster-boundaries --download
```

Set `TERRACOTTA_GDAL_LIBRARY`, `GDAL_DATA`, and `PROJ_DATA` for the native runtime.
Use `--reference-archive ARCHIVE` if the pinned sdist is outside the default
`.cache/reference/source.tar.gz`. The runner verifies its checksum and application
source. The generated reference version must report 0.10.1. Omit `--download` for
offline runs with verified sources in `.cache/raster-geography`.

`fixtures/geography-sources.json` pins three Copernicus DEM publisher tiles: 78°N
and the adjacent 179°E/180°W tiles. These are Copernicus DEM 2021 inputs supplied
through the public Sinergise distribution. Native crops around real terrain and
the dateline mosaic retain source values and resolution. The mosaic concatenates
adjacent columns, unwrapping western longitude by +360°. Derived EPSG:3413, 3035,
and 3857 inputs use declared nearest reprojection of real pixels.

The seven maintained corpus rasters supply UInt8 classes, UInt16 reflectance,
Float32 elevation, and natural nodata edges. Rotated derivatives deliberately
alter affine coefficients to isolate grid geometry while retaining real pixel
values. Dataset masks copy natural source validity. The RGBA derivative uses the
declared grayscale conversion `min(source // 16, 255)` and copies natural validity
to alpha. It isolates image mask semantics rather than acquiring another scene.
Average and nearest overviews are explicitly built at factors 2/4/8. Reopening
must preserve the declared masks, alpha interpretation, and overview levels.

Both profiles run all 16 nearest/linear/cubic/average warp/read pairs on previews,
overview-scale outputs, source edges, detail windows, and value-preserving reads.
Geographic cases also read both sides of the meridian. Sparse coverage must raise
the expected out-of-bounds error. Full/sample/chunk metadata are compared with
the pinned oracle. Deliberate native rotated hull changes use independent affine
pixel-square geometry. Existing narrow metadata acceptance bounds remain explicit.
Raw array shape, dtype, mask, and valid values are checked separately, followed
by exact decoded singleband/compute/RGB and partial-channel RGB images. Native
transparent-image dimensions use their own documented expectation.

The runner copies the shared library before execution and records its hash,
source identities, fixture recipes/hashes, oracle identity, per-case arrays,
decoded images, and difference counts. It exits unsuccessfully for any mismatch,
missing case, or unexpected oracle error. This is a shared-core correctness check.
Installed-wheel/platform qualification remains separate. Native direct reads and
opt-in samplers are explicitly disabled, no new numerical tolerance is inferred,
and no benchmarks are run.
The correctness workflow runs this same matrix and retains a separate report
artifact. A configured workflow does not establish an execution or a pass.
The runner also rejects source changes during a run.
