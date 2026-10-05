# Current design decisions

These decisions describe the current implementation. Public support and interface
contracts are defined in `docs/` and enforced by maintained tests. A configured
target is not a claim that its current release artifact has passed qualification.

## One shared core with explicit adapter boundaries

The Rust core owns raster reads, metadata, expressions, storage, and HTTP-contract
handling. Native HTTP/CLI, Python, WSGI, and Lambda use that core. Keep
transport parsing and Python-specific representations at their boundaries so
fixes do not fork rendering or catalog semantics between entry points.

Python provides native byte-oriented APIs and shims for documented settings,
driver/provider, masked-array and Future workflows. Validation, queries, writes,
lazy metadata, and raster operations execute in Rust. NumPy representation and
Future wrapping remain Python boundary responsibilities. Both CLI profiles use
the native executable; the documented WSGI entry point uses its native adapter.
Flask/Click extension hooks, raw SQLAlchemy connections, and internal image/raster
helper modules are outside this public contract. The native server executes
without Python; the ordinary installation supplies NumPy for documented array
workflows. The pinned contract follows Terracotta 0.10.1's `docs/api.rst`,
`docs/settings.rst`, documented WSGI deployment and top-level `__all__`.
Imports used to build its colormap documentation gallery do not define an
application API. Importability and internal unit tests alone do not establish
public support. The CLI launcher locates and executes its bundled binary without
importing the Python API or parsing configuration. Raster values and masks cross
the C boundary as binary buffers; Python supplies their NumPy/Future representation.

See [architecture](../docs/explanation/architecture.md), `src/python.rs`,
`src/ffi.rs`, and `python/rcfp/`. Revisit the boundary if a supported adapter needs
streaming or an interface that cannot be expressed without duplicating core logic.

## Retain GDAL/PROJ with deliberate ownership

Load the stable GDAL C API dynamically and keep raster/projection orchestration
in Rust. GDAL handles and VRTs belong to their worker; initialization/registration
is serialized, and the library handle lives for the process. Native thread-local
destructors must execute while the loaded library is still mapped.

An application owns a bounded raster queue, worker threads, caches, and remote
snapshot leases. Destruction drains and joins workers, including partial startup
failure paths. Response deadlines and client disconnects do not cancel GDAL;
admitted work stays accounted for until it finishes. Reuse persistent applications
instead of constructing a pool for every request.

See `src/execution.rs`, `src/raster.rs`, and
[resource guidance](../docs/how-to/resources.md). Revisit cancellation only with
a demonstrably safe native mechanism. A GDAL-free backend needs separate fidelity,
format, projection, mask, overview, and packaging evidence.

## Separate release, profile, protocol, and catalog identities

The distribution, primary import, and console command are `rcfp`. Hatch VCS derives
the package version from Git and supplies it to archives and installed metadata.
HTTP protocol identity remains `0.10.1`; current catalogs use Terracotta format
`0.10`. A new RCFP release number must not reject compatible databases or clients.
No upstream import or console shim is installed; users update names explicitly.

Default profile `native` and named profile `terracotta-0.10.1` are immutable
application/process policies. Resolve CLI option > TOML > environment > default
before Python import; Python freezes its selector at first import. Request settings
cannot change the profile. Supported current catalogs require no migration or
reingestion when selecting it.

Keep the named profile's public workflows and pinned reference gates in every
release that ships it. There is no planned removal date; removal requires announced
support policy and a major release. A future native-only format needs a distinct
marker, rejection checks, and explicit migration and rollback documentation.
See [compatibility](../docs/compatibility.md),
[native differences](../docs/native-migration.md), and `src/compatibility.rs`.

## Preserve database semantics and publish immutable remote snapshots

SQL engines own filtering, ordering, collation, and pagination. Do not emulate
these by fetching all rows and sorting/filtering in Rust. Existing bounds mapping,
float32 percentile encoding, dynamic key order, and supported metadata types are
part of the format. Unsupported markers are rejected before writes.

Lazy metadata computation is coordinated before publishing results. Native inserts
without metadata remove old metadata; compatibility inserts can retain it. Native
CLI ingest validates ordered key names and uses a whole-ingest transaction.
Legacy ingest and connection contexts commit individual operations; callers
requesting atomic Python writes use `DataStore.batch`. SQLite/PostgreSQL migrations are transactional;
MySQL DDL can commit partially and needs backup restoration after failure.

Remote SQLite remains a read-only whole-file TTL snapshot. Validate a new private
generation before publication and retain old files while readers lease them.
Failed refresh reports an error and permits a later retry. Resolve AWS credentials
at refresh so configured profiles and rotating credentials can be used. Metadata
credentials remain distinct from raster-specific GDAL settings.

See `src/storage/`, [catalogs](../docs/reference/catalogs.md), and
[backup/migration](../docs/how-to/backup-migrate.md). A portable byte encoding or
different transaction contract requires an explicit format/interface change.

## Package a private native runtime and qualify the final artifact

Wheels carry the executable, shared core, limited-API extension, GDAL/PROJ
libraries/data, browser assets, and licenses together. Rasterio is a fixture/test
tool, not an installed runtime dependency or an implicit GDAL link target. Native
runtime acquisition uses checksum-locked conda-forge archives. Keep legal notices
and dependency closure with each artifact.

Linux wheels are built/repaired on the supported Ubuntu architecture runners;
auditwheel's CPU ISA check stays enabled. macOS/Windows repair closes native
dependencies. `cp312-abi3` artifacts are installed and tested on CPython 3.12–3.14
for each of the five OS/architecture targets. Build Lambda separately inside
AL2023 and test in the official runtime image; wheel and Lambda libc baselines
are independent. Install Python's Rustup helper after native Cargo compilation
in hosted wheel jobs because its Cargo shim can shadow the runner's toolchain.

Source archives include supported build inputs, tests, and user documentation.
Developer context, caches, and generated HTML are excluded. Preserve full Git
history for release versioning and the generated version file in source archives.
Publish only exact repaired wheels and the sdist whose hashes, versions, metadata,
and required gates were checked. See `packaging/`, `.github/workflows/`, and
[artifact requirements](../docs/reference/artifacts.md).

Wheel and source builders explicitly emit core metadata 2.4, which contains all
fields this package needs and is understood by the pinned release validator.
Keep the [Hatch metadata setting](https://hatch.pypa.io/latest/plugins/builder/wheel/)
explicit rather than inheriting newer backend defaults. Hatch VCS remains the
version source; changing the metadata version does not change package versioning.

## Optimize only within measured input and contract boundaries

The general GDAL path remains the fallback. Compatible affine Web Mercator reads,
worker-local geometry caching, bounded decoded blocks, and idle neighbor prefetch
are opt-in optimizations with their own applicability checks. The native-only UTM
sampler changes the sampling footprint; compatibility mode disables that sampler
while retaining the exact fast paths and prefetch. Check policy at setting access,
not by adding per-pixel branches.

Measure first viewport, new-tile pan completion, and loaded p95 separately, with
the same immutable inputs, CPU affinity, total cache budgets, and multiple paired
fresh-process trials. Include all Python workers' GDAL/application caches.
Logical cache equality does not imply equal physical memory, and summed RSS is
not private-memory usage. Image differences require their own acceptance evidence.

Cold projection initialization and distinct source-block reads limit first views.
Prior block singleflight and reusable-transformer experiments did not establish
a reliable first-view gain and are not retained. Prewarming must include startup
cost. Broader CRS/dtype/mask/overview/remote inputs and tail latency need measurement
before extending a fast-path claim or enabling it by default. Current reproducible
measurements are in [performance](../docs/explanation/performance.md).

## Bound ingress and require verified remote transport

Socket parsing, request headers/bodies, render dimensions, response counts, GDAL
buffer lengths, and WSGI inputs have explicit bounds. Raster execution and HTTP
admission are bounded separately. HTTP render admission follows the configured
worker-plus-queue capacity, with the raster queue retaining its own overload
check. Sanitize native raster errors and hide metrics
unless configured. Keep these checks at both transport and native boundaries.

MySQL/PostgreSQL require certificate and hostname verification by default.
`sslrootcert` supports private CAs. Explicit `sslmode=disable` is accepted only for
a literal loopback IP and without a root certificate. Reject weaker remote modes
and options bypassing verification. Unit rejection checks and successful local
plaintext connections do not establish verified-TLS handshakes.

Compatibility SQL drivers cache by process, catalog identity, and a private hash
of the complete connection URL. Different credentials or TLS options must not
reuse an existing session. Public catalog identities remain credential-free;
equivalent local SQLite paths still share a driver.

See `src/http/transport.rs`, `src/storage/relational.rs`, Python connection
driver wrappers, and [validation tasks](human-validation.md). Dependency advisory scans are
point-in-time checks and do not qualify native libraries or deployments.

## Native tile output grids and cache ownership

Native tiles now read directly into the requested map grid through GDAL/PROJ.
Contained north-up Web Mercator windows use floating RasterIO; other geometries
use a single requested-grid warped VRT with GDAL overview selection. The path is
default in native mode and disabled by the Terracotta profile. Previews retain
the general reader. One-based tile band selectors permit shared same-file RGB
jobs without changing the SQLite/catalog format; metadata remains first-band,
so nondefault bands require explicit numeric stretches. Native COG map-grid
preparation is optional and explicitly changes sampling. Block size is selectable:
remote range round trips made universal 256-pixel blocks a poor choice.

Workers own dataset handles. Local generations include raster/mask/overview/aux
file stamps. Remote handle leases are bounded and opt-in; a lease is not an ETag
freshness contract and does not clear GDAL's downloaded cache. Use revisioned
paths for remote/VRT dependency changes. Encoded images/coalescing have a separate
bounded budget and remote TTL; error/panic cleanup wakes followers. SQLite
optional descriptors use data_version, own-write invalidation and coherent
snapshots; transactions bypass retention. These caches remain optional because
spatial/style reuse and memory tradeoffs differ across installations.

RGB batching removes per-request coordination threads; matching bands share one
source/warp and other reads share the bounded worker queue. Dropping a queued
waiter marks that work abandoned. Active GDAL work still drains normally, and
stateless tile URLs do not identify a viewport lifecycle. Retain the existing
worker/admission limits instead of source serialization or extra thread pools.

Transform-free rendering fuses stretch and packing with the existing f64/order/
rounding semantics. Direct PNG compression removes redundant nondefault encoding.
Native RGB optionally uses direct libwebp display encoding with explicit quality
and preserved alpha. PNG stays default; the measured WebP CPU/byte tradeoff and
all other ranked dispositions are in [performance](performance.md). Typed public
buffers, manual SIMD, connection pools and automatic prefetch need evidence of
a bottleneck that these simpler changes do not address.
