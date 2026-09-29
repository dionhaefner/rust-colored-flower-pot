# Running RCFP

The Linux x86_64 native artifact requires glibc 2.34 or newer; wheels require
CPython 3.12–3.14 and a matching operating system. Protocol and database
compatibility is 0.10.1; the RCFP release version is 0.1.0.

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
Rust executable are bundled inside that package. The pip-installed command uses
the bundled Rust CLI. The source build needs Rust 1.85.1 and a C compiler; the abi3 wheel supports CPython 3.12–3.14 on its matching OS.

The independent executable uses the same native raster/storage core:

```sh
dist/bundle/bin/rcfp serve -d /absolute/path/catalog.sqlite --bind 127.0.0.1:5000
dist/bundle/bin/rcfp connect localhost:5000 --no-browser --port 5100
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
metadata; `rcfp.DataStore.batch` applies an explicitly atomic data batch.

## WSGI and configuration

```python
# app.py; run with your existing WSGI host
from rcfp.wsgi import Application
application = Application("/data/catalog.sqlite")
```

Reuse one WSGI application across requests so its native worker pool and
cache stay alive. `rcfp.NativeApplication` exposes the same session for direct
request handling.

RCFP accepts Terracotta 0.10.1 defaults and `TC_*` environment variables.
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

S3 metadata snapshots are read-only. Terracotta 0.10.1 downloads and
validates eagerly, refreshes on reconnect after TTL expiry, and pins its current
generation inside a connection context. RCFP native HTTP refreshes between
requests. Failed refreshes preserve the previous file but return an error.
Standard AWS credentials and `AWS_ENDPOINT_URL_S3` govern metadata; legacy
`TC_RASTER_AWS_*` settings govern raster access. MySQL and PostgreSQL
connections require TLS with certificate and hostname verification. PostgreSQL
accepts `sslmode=verify-full`; `sslrootcert` can add a private CA certificate.

## Logs, profiling and resource limits

Terracotta 0.10.1 log-level/tracing decorators and Werkzeug
`serve --profile` path are not installed by RCFP. The RCFP executable's
`--profile` emits JSON request records
with `elapsed_ms`, status, method and path; it does not provide Python call-level
statistics. Native `--debug` adds diagnostics but no interactive debugger or
automatic reloader. These are the supported operational interfaces.

`XRAY_PROFILE` enables native daemon subsegments for sampled incoming trace IDs.
Set `AWS_XRAY_DAEMON_ADDRESS` for the daemon. Terracotta 0.10.1 used
the optional AWS X-Ray SDK; it is not part of the RCFP wheel.

Native bounds: `TC_RASTER_WORKERS` (default 3, or 1 with multiprocessing disabled),
`TC_RASTER_QUEUE_CAPACITY` (8), `TC_RASTER_TIMEOUT_MS` (30000), and
`TC_RASTER_CACHE_BYTES` (overrides the legacy `RASTER_CACHE_SIZE`). Native caches
count uncompressed arrays; Terracotta 0.10.1 uses a compressed LFU cache and
compression setting. Budgets with the same number need not retain the same number of images.
The HTTP socket adapter admits a fixed number of connections, limits request
headers to 16 KiB and bodies to 1 MiB, and closes requests that take more than
five seconds to arrive. Render requests are limited to two at a time. Tile
sizes are limited to 1,048,576 pixels; `/colormap` accepts at most 4,096
samples and `/datasets` returns at most 1,000 items per page.

`/__terracotta/metrics` is hidden by default. Set `TC_EXPOSE_METRICS=true`
to expose queue, cache, worker, drain and resource data to clients.
Queue saturation returns 503. A response deadline returns 504;
neither it nor a client disconnect forcibly cancels GDAL. Submitted work drains
and remains accounted. Dropping an application closes its queue and releases
workers/cache once outstanding native work finishes. Worker threads are joined
before application destruction returns, including native thread-local cleanup.
This wait can exceed the response deadline; persistent services
should retain their application and configure bounded native remote I/O.

## Database backups and schema upgrades

RCFP reads compatible 0.10 catalogs without a schema upgrade. To upgrade an
older supported catalog, stop writers, create an engine-consistent backup, and
run `rcfp migrate DATABASE` against a restored copy first. The command accepts
`--from VERSION` when the stored version cannot be read, `--to VERSION` to
select a supported target, and `-y` to confirm without a prompt. Check catalog
keys, metadata, and representative images before using the upgraded catalog.

For SQLite, use the backup API so committed WAL data is included. For MySQL or
PostgreSQL, use a consistent engine dump or snapshot. SQLite and PostgreSQL
upgrade chains are transactional. MySQL DDL can commit partially; restore the
backup after a failed upgrade before retrying. The Terracotta 0.7 MySQL upgrade
chain is unsupported. S3 snapshots are read-only and cannot be upgraded in place.

## Container and Lambda artifacts

`packaging/container/Dockerfile` consumes a `bundle/` and `fixture/` build context;
the latter can contain a local catalog or be replaced by a mounted data directory. Bind the native server to the intended interface.

`packaging/build-lambda.sh BUNDLE OUTPUT` makes a custom-runtime zip containing
`bootstrap` and `runtime/`. Configure `TC_DRIVER_PATH` (and optional provider),
normal AWS credentials and runtime memory/time limits in the deployment.
The bootstrap starts the native Lambda Runtime API loop. API Gateway v1/v2
events and binary PNG/base64 replies are supported.
