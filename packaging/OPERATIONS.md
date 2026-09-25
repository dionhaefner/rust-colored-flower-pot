# Running the replacement

P08 qualifies Linux x86_64 locally. The native artifact requires glibc 2.34 or
newer; the wheel additionally requires the matching CPython ABI (currently
CPython 3.12). Other operating systems and architectures have prepared CI jobs,
not passing release evidence. Protocol/database compatibility remains 0.10.1;
the implementation and wheel version is 0.1.0.

## Install and run

Install the wheel in its own virtual environment, without the original
`terracotta` distribution in that environment:

```sh
python -m pip install -c packaging/python-dependencies-linux-cp312.lock validation/artifacts/p08-final-wheels/*.whl
terracotta --help
terracotta serve -d /absolute/path/catalog.sqlite
terracotta connect localhost:5000
```

The wheel supplies both `import terracotta` and `import terracotta_rust`.
The `terracotta` console entry point retains the legacy Click workflows. Install
`terracotta-rust[operations]` when Python X-Ray/color logging, raw SQL migration
drivers or Python AWS helper functions are needed, and `[colormaps]` for palette
generation. Core native SQL and S3 access does not depend on these Python extras.
For a local wheel use `python -m pip install './path/to/wheel.whl[operations]'`.

The independent executable uses the same native raster/storage core:

```sh
validation/artifacts/p08-final/bin/terracotta serve -d /absolute/path/catalog.sqlite --bind 127.0.0.1:5000
validation/artifacts/p08-final/bin/terracotta connect localhost:5000 --no-browser --port 5100
```

Keep the bundle's `bin`, `lib`, `share`, and `licenses` directories together.
Do not copy just its executable to another machine. `TERRACOTTA_RUNTIME_ROOT`
selects an explicit bundle; `TERRACOTTA_GDAL_LIBRARY` selects an external GDAL
shared library. The external library's dependencies and GDAL/PROJ data must be
installed and discoverable by the operating system.

## Existing Python workflows

```python
import terracotta as tc

tc.update_settings(USE_MULTIPROCESSING=False)
driver = tc.get_driver("catalog.sqlite")
driver.create(["scene", "band"])
with driver.connect():
    metadata = driver.compute_metadata("/data/red.tif", extra_metadata={"source": "Sentinel-2"})
    driver.insert(("lagoon", "red"), "/data/red.tif", metadata=metadata)
image = driver.get_raster_tile(("lagoon", "red"))
future = driver.get_raster_tile(("lagoon", "red"), asynchronous=True)
assert future.result().shape == image.shape
```

Metadata computation returns NumPy percentile arrays and tuple bounds; database
reads return float32-decoded percentile lists. Public connection contexts yield
SQLAlchemy connections and nested contexts reuse that object. Driver methods
use the native store. As in the reference, successful method calls commit
individually: an exception in an outer context does not roll them all back.
Use `terracotta_rust.DataStore.batch` for an explicitly atomic structured batch.
Historical migration scripts and caller-provided SQL use the retained SQLAlchemy
boundary; they are not statements executed by the Rust SQL API.

Raster futures use a three-process **spawn** pool when `USE_MULTIPROCESSING=True`.
Guard executable scripts with `if __name__ == "__main__":`, or disable
multiprocessing for embedded/interactive callers. The false setting uses a
single Python worker thread. Workers receive an isolated settings snapshot.

## WSGI and configuration

```python
# app.py; run with your existing WSGI host
from terracotta_rust.wsgi import Application
application = Application("/data/catalog.sqlite")
```

For Flask integrations, `terracotta.server.create_app()` preserves blueprints,
validation and debug handling; set `tc.update_settings(DRIVER_PATH=...)` first.
Applications own their native worker pool and cache for their lifetime. Reuse
the application across requests. The lower-level stateless `request()` helpers
remain compatible, but construct an application per call.

Legacy defaults and `TC_*` environment variables are parsed by both boundaries.
Explicit TOML configuration overrides environment values; explicit CLI flags
override the resulting settings:

```sh
terracotta -c settings.toml --loglevel info serve -d catalog.sqlite
```

TOML uses unprefixed names such as `DEFAULT_TILE_SIZE = [256, 256]` and
`USE_MULTIPROCESSING = false`. The deprecated MYSQL/POSTGRESQL user/password
aliases warn and defer to explicit SQL_USER/SQL_PASSWORD. Settings snapshots
are captured when native applications/sessions start; create a new application
to apply a changed configuration. Do not mutate process environment variables
while other applications are serving requests.

S3 metadata snapshots are read-only. The Python remote driver downloads and
validates eagerly, refreshes on reconnect after TTL expiry, and pins its current
generation inside a public connection context. Native HTTP refreshes between
requests. Failed refreshes preserve the previous file but return an error.
Standard AWS credentials and `AWS_ENDPOINT_URL_S3` govern metadata; legacy
`TC_RASTER_AWS_*` settings govern raster access. Actual AWS services remain
unqualified; local emulator success does not establish cloud access.

## Logs, profiling and resource limits

The Python log-level and tracing decorators are retained. Python `serve --profile`
uses maintained Werkzeug profiling middleware; native calls appear as extension
boundary time. The native executable's `--profile` emits JSON request records
with `elapsed_ms`, status, method and path; it does not provide Python call-level
statistics. Native `--debug` adds diagnostics but no interactive debugger or
automatic reloader. These are explicit operational alternatives (D-009/RW-032).

`XRAY_PROFILE` enables native daemon subsegments for sampled incoming trace IDs.
Set `AWS_XRAY_DAEMON_ADDRESS` for the daemon. The retained Python decorator uses
the optional AWS X-Ray SDK. P07 checks local UDP serialization and propagation;
AWS ingestion and account configuration are not tested.

Native bounds: `TC_RASTER_WORKERS` (default 3, or 1 with multiprocessing disabled),
`TC_RASTER_QUEUE_CAPACITY` (8), `TC_RASTER_TIMEOUT_MS` (30000), and
`TC_RASTER_CACHE_BYTES` (overrides the legacy `RASTER_CACHE_SIZE`). Native caches
count uncompressed arrays; Python retains its compressed LFU cache and compression
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

[The workflow](../.github/workflows/platforms.yml) runs on manual dispatch or
relevant pull requests. Its matrix covers Linux x86_64/arm64, macOS Intel/arm64,
and Windows x86_64. Separate Linux jobs build and smoke-test Lambda artifacts
on both architectures. It publishes test artifacts to the workflow run only;
it contains no package publishing or cloud deployment step.

The platform wheel stage intentionally uses installed conda GDAL; those wheels
are qualification artifacts requiring that runtime. The standalone Linux wheel
uses `build-wheel.sh` and the portable native bundle. CI downloads a checksum-
pinned real Sentinel-2 parent, takes the established native-pixel subset, and
compares rendered RGBA to the retained golden image. Uploads include reports,
images and wheels/zips. Failed jobs remain failed; no `continue-on-error` gate
turns them green. A first run must still validate the OS toolchains and dependency
solver on each runner. Runner labels follow [GitHub's hosted-runner list](https://docs.github.com/en/actions/reference/runners/github-hosted-runners);
environment activation follows [setup-micromamba](https://github.com/mamba-org/setup-micromamba).

Per the user's P07 scope change, run these jobs later and retain their evidence
before declaring those platforms supported. P08 adds physical BigTIFF offset,
polar/dateline, soak, rollback and fair performance evidence. Actual AWS/S3
services, deferred platforms and external basemap qualification remain release
gates. See [release notes](RELEASE_NOTES.md), [rollback instructions](ROLLBACK.md)
and the [P08 evidence index](../validation/evidence/p08/README.md).

The unchanged map client can issue a burst of many tile requests. P08 browser
qualification uses `TC_RASTER_QUEUE_CAPACITY=64`; the default queue of eight can
return bounded overload 503s during this burst. Size the queue for the expected
client concurrency and observe `/__terracotta/metrics`. Worker concurrency and
cache limits remain independently bounded. The qualification checks every
backend browser response, in addition to inspecting the final imagery.
