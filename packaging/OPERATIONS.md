# Running the replacement

P08 qualifies Linux x86_64 locally. The native artifact requires glibc 2.34 or
newer; the wheel additionally requires the matching CPython ABI (currently
CPython 3.12). Other operating systems and architectures have prepared CI jobs,
not passing release evidence. Protocol/database compatibility remains 0.10.1;
the implementation and wheel version is 0.1.0.

## Install and run

Install the current RCFP source in a virtual environment:

```sh
python -m pip install .
rcfp --help
rcfp serve -d /absolute/path/catalog.sqlite
rcfp connect localhost:5000
```

The source-built wheel installs the `rcfp` distribution, the `rcfp` Python
package, and the `rcfp` CLI. Its native extension, GDAL/PROJ runtime, and
Rust executable are bundled inside that package. The retained Terracotta
Python sources are comparison material and are excluded from the wheel.
The pip-installed command uses the bundled Rust CLI. The source build needs
Rust 1.85.1 and a C compiler; the wheel needs a matching Python ABI and OS.

The independent executable uses the same native raster/storage core:

```sh
validation/artifacts/p08-final/bin/rcfp serve -d /absolute/path/catalog.sqlite --bind 127.0.0.1:5000
validation/artifacts/p08-final/bin/rcfp connect localhost:5000 --no-browser --port 5100
```

Keep the bundle's `bin`, `lib`, `share`, and `licenses` directories together.
Do not copy just its executable to another machine. `TERRACOTTA_RUNTIME_ROOT`
selects an explicit bundle; `TERRACOTTA_GDAL_LIBRARY` selects an external GDAL
shared library. The external library's dependencies and GDAL/PROJ data must be
installed and discoverable by the operating system.

## Python API

```python
import rcfp

store = rcfp.DataStore("catalog.sqlite")
store.create(["scene", "band"])
store.insert(["lagoon", "red"], "/data/red.tif")
status, content_type, body, cacheable = rcfp.request(
    "catalog.sqlite", "GET", "/singleband/lagoon/red/preview.png"
)
assert status == 200 and content_type == "image/png"
```

`rcfp.render_preview` returns PNG bytes; `rcfp.compute_metadata` computes raster
metadata; `rcfp.DataStore.batch` applies an explicitly atomic data batch. The
retained Terracotta Python driver and Flask application are reference code and
are not installed as RCFP APIs.

## WSGI and configuration

```python
# app.py; run with your existing WSGI host
from rcfp.wsgi import Application
application = Application("/data/catalog.sqlite")
```

Reuse one WSGI application across requests so its native worker pool and
cache stay alive. `rcfp.NativeApplication` exposes the same session for direct
request handling.

Legacy defaults and `TC_*` environment variables are parsed by both boundaries.
Explicit TOML configuration overrides environment values; explicit CLI flags
override the resulting settings:

```sh
rcfp -c settings.toml --loglevel info serve -d catalog.sqlite
```

TOML uses unprefixed names such as `DEFAULT_TILE_SIZE = [256, 256]` and
`USE_MULTIPROCESSING = false`. The deprecated MYSQL/POSTGRESQL user/password
aliases warn and defer to explicit SQL_USER/SQL_PASSWORD. Settings snapshots
are captured when native applications/sessions start; create a new application
to apply a changed configuration. Do not mutate process environment variables
while other applications are serving requests.

S3 metadata snapshots are read-only. The original Terracotta Python reference downloads and
validates eagerly, refreshes on reconnect after TTL expiry, and pins its current
generation inside a connection context. RCFP native HTTP refreshes between
requests. Failed refreshes preserve the previous file but return an error.
Standard AWS credentials and `AWS_ENDPOINT_URL_S3` govern metadata; legacy
`TC_RASTER_AWS_*` settings govern raster access. Actual AWS services remain
unqualified; local emulator success does not establish cloud access.

## Logs, profiling and resource limits

The original Terracotta Python log-level/tracing decorators and Werkzeug
`serve --profile` path are not installed by RCFP. The RCFP executable's
`--profile` emits JSON request records
with `elapsed_ms`, status, method and path; it does not provide Python call-level
statistics. Native `--debug` adds diagnostics but no interactive debugger or
automatic reloader. These are explicit operational alternatives (D-009/RW-032).

`XRAY_PROFILE` enables native daemon subsegments for sampled incoming trace IDs.
Set `AWS_XRAY_DAEMON_ADDRESS` for the daemon. The original Python reference used
the optional AWS X-Ray SDK; it is not part of the RCFP wheel. P07 checks local UDP serialization and propagation;
AWS ingestion and account configuration are not tested.

Native bounds: `TC_RASTER_WORKERS` (default 3, or 1 with multiprocessing disabled),
`TC_RASTER_QUEUE_CAPACITY` (8), `TC_RASTER_TIMEOUT_MS` (30000), and
`TC_RASTER_CACHE_BYTES` (overrides the legacy `RASTER_CACHE_SIZE`). Native caches
count uncompressed arrays; the original Python reference uses a compressed LFU cache and compression
setting. Budgets with the same number need not retain the same number of images.
Inspect `/__terracotta/metrics` for queue, cache, worker, drain and resource data.
Restrict this diagnostic endpoint at the service boundary if required by the
deployment. Queue saturation returns 503. A response deadline returns 504;
neither it nor a client disconnect forcibly cancels GDAL. Submitted work drains
and remains accounted. Dropping an application closes its queue and releases
workers/cache once outstanding native work finishes. P08 joins those workers
before application destruction returns, including native thread-local cleanup
(RW-039/D-011). This wait can exceed the response deadline; persistent services
should retain their application and configure bounded native remote I/O.

## Container and Lambda artifacts

`packaging/container/Dockerfile` consumes a `bundle/` and `fixture/` build context;
the latter can contain a local development catalog, or be replaced by a mounted
production data directory. Bind the native server to the intended interface.
The retained clean-container test runs with network disabled at runtime.

`packaging/build-lambda.sh BUNDLE OUTPUT` makes a custom-runtime zip containing
`bootstrap` and `runtime/`. Configure `TC_DRIVER_PATH` (and optional provider),
normal AWS credentials and runtime memory/time limits in the deployment.
The bootstrap starts the native Lambda Runtime API loop. API Gateway v1/v2
events and binary PNG/base64 replies are supported. Local AL2023 load/render and
event-adapter tests pass. No AWS function was created or invoked.

## Platform CI and remaining qualification

[CI and PyPI release](CI.md) documents the GDAL bundle, five repaired wheel
builds, clean pip installation tests, native/Lambda qualification and protected
tag publication. RCFP pins `rasterio==1.4.4` as a Python dependency and embeds a
private GDAL/PROJ runtime because its Rust core calls GDAL directly. The wheel
includes the `rcfp` executable; the standalone native bundle is also produced
separately. CI tests the final repaired wheel files before publication.

The workflows have not run on hosted macOS, Windows or arm64 runners yet. The
local Linux CPython 3.12 wheel passed the pinned real-raster installed-package
smoke. Historical P08 qualification remains local and predates the current RCFP
package boundary. Actual AWS/S3 and external basemap checks remain open.

The unchanged map client can issue a burst of many tile requests. P08 browser
qualification uses `TC_RASTER_QUEUE_CAPACITY=64`; the default queue of eight can
return bounded overload 503s during this burst. Size the queue for the expected
client concurrency and observe `/__terracotta/metrics`. Worker concurrency and
cache limits remain independently bounded. The qualification checks every
backend browser response, in addition to inspecting the final imagery.
