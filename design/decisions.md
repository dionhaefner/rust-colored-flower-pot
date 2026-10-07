# Current design decisions

These decisions describe the current implementation. Public support and interface
contracts are defined in `docs/` and enforced by maintained tests. A configured
target is not a claim that its current release artifact has passed qualification.

## One shared core with explicit adapter boundaries

The Rust core owns raster reads, metadata, expressions, storage, and HTTP-contract
handling. Native HTTP/CLI, Python, WSGI, and Lambda use that core. Keep
transport parsing and Python-specific representations at their boundaries so
fixes do not fork rendering or catalog semantics between entry points.

Keyed catalogs adapt SQL stores, persisted native band/expression bindings and
STAC selections behind the same metadata, array, application and HTTP interfaces.
The `rcfp-native-2` SQLite format reuses key/dataset/metadata tables and stores
source snapshots and logical declarations in database columns. Source detection is shared by CLI, Python, WSGI
and Lambda. Provider search queries, credentials and discovery budgets belong to
local deployment configuration; render parameters stay in the common requests.

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

Native mode uses async-tiff 0.4.0 for eligible TIFF dimensions, band counts,
embedded nodata and contained EPSG:3857 reads that select stored pixels exactly.
This replaces format access while preserving
the existing sampling boundary. Detailed asset metadata, projection, resampling,
sidecars, unsupported layouts/codecs and configured GDAL access use GDAL. The
Terracotta profile always uses GDAL. `TC_ASYNC_TIFF=false` restores GDAL format
access in native mode. See [TIFF qualification](async-tiff-evaluation.md).

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
HTTP protocol identity remains `0.10.1`. Terracotta catalogs use format `0.10`,
native keyed catalogs use `rcfp-native-2`, and advanced layers retain `rcfp-native-1`. A new RCFP release number must not
reject compatible databases or clients.
No upstream import or console shim is installed; users update names explicitly.

Default profile `native` and named profile `terracotta-0.10.1` are immutable
application/process policies. Resolve CLI option > TOML > environment > default
before Python import; Python freezes its selector at first import. Request settings
cannot change the profile. Supported current catalogs require no migration or
reingestion when selecting it.

Keep the named profile's public workflows and pinned reference gates in every
release that ships it. There is no planned removal date; removal requires announced
support policy and a major release. Native layer registration and import use a
separate database, with explicit migration and rollback documentation.
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

## Publish guarded rendered revisions

Rendered publication uses shared application ETags, revalidated mutable URLs
and digest-guarded immutable image URLs. Shared caching is an explicit operator
choice; authorization/cookie requests and errors use no-store. Encoded image
reuse and request coalescing retain their existing bounded ownership. See
[rendered caching](rendered-caching.md) for identity and freshness boundaries.

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

The general GDAL path remains the fallback. The older affine Web Mercator reads,
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
Prior cross-worker block singleflight and cross-file transformer experiments did
not establish a reliable first-view gain. Native worker-local affine plans now
avoid repeated utility setup and benefit navigation; first-view initialization
remains a separate limit. Prewarming must include startup
cost. Broader CRS/dtype/mask/overview/remote inputs and tail latency need measurement
before extending a fast-path claim or enabling it by default. Current reproducible
measurements are in [performance](../docs/explanation/performance.md).

## Bound ingress and require verified remote transport

Application-owned fixed-stage histograms cover selection, raster queue wait,
source lookup/open, GDAL raster work, coloring, encoding, coalesced waits and
response readiness. Native transport additionally measures HTTP queue wait and
socket delivery. Request timing contexts follow admitted raster jobs across
workers; deadline traces are snapshots, while later completions still update
application aggregates. Remote opening/reading have fixed subset stages rather
than source labels. GDAL I/O and decode/reprojection remain combined where the
C API cannot separate them reliably. Protected JSON and Prometheus diagnostics
share the same counters; sampled X-Ray retains its existing daemon integration.
No source identity or raw URL enters metrics, timing logs or trace routes.

Retain bounded FIFO foreground admission, active render coalescing and queued
waiter abandonment. Idle prefetch checks admitted foreground work even when
another worker owns the receiver lock; executing native work is never forcibly
cancelled. Isolated controls with equal budgets show successful foreground
viewport and tail improvements from active sharing and queued expiry. The
controlled-origin evidence and its scope are in [validation](human-validation.md).
Per-source fairness and new cost policies require a measured workload
benefit before changing ordering. HTTP server-loop exit owns and joins transport
workers; Python capsule destruction releases the GIL during native pool joining.
Standalone CLI signal handling closes the listener before draining owned workers.
Its handler wakes blocking accept through a local connection, avoiding polling
on the foreground path. Embedding does not install process signal handlers.

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
file stamps. Remote handle leases default to 60 seconds in native mode and zero in the named
profile, with explicit zero honored. A lease is not an ETag
freshness contract and does not clear GDAL's downloaded cache. Use revisioned
paths for remote/VRT dependency changes. Finished images have a separate bounded budget and remote TTL. Active identical
renders coalesce even with zero retention; admission bounds active flights and
error/panic cleanup wakes followers. SQLite
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
all other ranked dispositions are in [performance](performance.md). Public buffers remain f64. Runtime AVX2 preserves normalization operation order
and ties with scalar fallback. Lossless private typed caching stays opt-in after
fresh-pan regressions, with compact and expanded hot entries sharing one budget.
Nondefault PNG levels use optional bundled libdeflate; level 1 retains fdeflate
after libdeflate regressed. Connection pools and automatic prefetch remain
discarded for the measured workload.

Eligible native affine sources own one base projection plan and at most four
overview plans per source/worker. Tile VRTs own only their approximation wrapper;
the source owns the base transformer and overview handles. Update destination
grids only while that worker owns the source. Drop plans before the root dataset.
AUTO overview selection follows bundled GDAL 3.10.3, retaining its 0.125-pixel
approximation. Reject GCP/RPC/geolocation, compound/three-axis CRS, mixed dtypes, misplaced
alpha and working-dtype promotion to the utility fallback. Warp only requested
bands when all data bands lack nodata and share coverage, otherwise retain all
bands to preserve nodata union. No fixture identity or CRS-zone allowlist is used.
Alpha and masks retain f64 reads after a Byte-mask experiment failed to
establish broad navigation gains.

## Database-backed native assets and layers

Native logical datasets use the existing keyed ingest and discovery model. The
`rcfp-native-2` SQLite database holds selected bands, raw/scaled semantics,
expression dependencies, display defaults and resolved STAC source snapshots.
Band reads reuse the native alignment/validity core. Regular clients use logical
keys through the shared metadata, arrays and tile routes. The `rcfp-native-1`
advanced layer API retains explicit overlap, category and EPSG grid behavior.
The Terracotta profile rejects native markers and preserves its qualified schema.

See [native layers](native-layers.md) for model rationale and
[layer contracts](../docs/reference/layers.md) for supported interfaces.

## Resolve STAC discovery before reading immutable asset revisions

Use the existing provider's POST STAC API Item Search index for bbox/time
selection. Complete bounded pagination and deterministic ordering once, then
retain a native selection separately from raster execution. Static Catalog and
Collection sources traverse `child`/`item` links once at startup under document,
Item and byte limits. Remote document links stay on one origin; local metadata
stays local. Large-catalog indexed filtering remains at an API provider. Search definitions
and resolved content have distinct canonical SHA-256 identities. Preserve the
legacy SQL format and profile; STAC is a native adapter, not a hosting service.

Require trusted SHA-256 revisions and verify whole self-contained TIFFs on first
metadata or render request before publishing private selection-owned files. This provides content
immutability even with mutable source URLs, keeps signing material out of cache
keys and handles, and isolates changed revisions without changing existing raster
caches. The initial whole-file cost is explicit; broader version-addressed range
reads require provider-specific authentication and byte-cache freshness evidence.

Public descriptors omit locations, arbitrary upstream properties and credentials.
CLI serving adapts its startup selection to the shared key-catalog discovery,
metadata, singleband, RGB and compute routes. Rendering choices belong to
requests. The SDK and viewer use normal dataset discovery and a generic source
revision for share links. CLI mappings or application callbacks refresh search headers and signed asset locations; 401/403
retries are bounded and errors are sanitized. Embedded GDAL georeferencing,
mask/alpha/nodata remain authoritative; STAC scale/offset applies after alignment.
See [STAC rationale](stac.md), [field contract](../docs/how-to/stac.md), and the
installed provider/revision checks in `tests/stac_contracts.py`.

## Public browser SDK and offline viewer

The dependency-free ES module SDK in `js/sdk` owns URL construction,
profile checks, fetch/error/abort semantics, catalog discovery and revisioned
shared state. The maintained viewer and independent Leaflet/MapLibre/OpenLayers
applications import those same public modules. Keep metadata, image sampling,
expressions, palettes and catalog ordering on the server. Display-pixel inspection
is explicitly RGBA. Native point inspection has a separate nearest source-cell
contract with declared calibration, validity and provenance. Time
selection uses an explicit catalog key, without inferring observation ranking.

The viewer uses original, readable modules and native labeled controls with
Leaflet. Its locally pinned assets make offline/no-token first use reproducible.
Framework removal is a consequence of the small maintained UI, not a quality
claim. Optional map adapters take libraries from callers so the SDK has no
runtime dependencies. Share state validates the expected service/profile/public
source revision and canonical render digest; immutable source identity remains
an operator responsibility until the server exposes a revision contract.
See [JavaScript](../docs/reference/javascript.md) for scope and support policy.

SDK versions and `sdk-v` tags are independent of Hatch VCS Python releases.
Hatch VCS Git discovery selects only `v` release tags so SDK tags cannot break
Python version extraction. A shared qualification workflow tests Node contracts,
strict declarations, installed
tarballs and Chromium server/viewer behavior for both release paths. npm publishing
uses a verified qualification artifact and job-scoped OIDC, without rebuilding or
package lifecycle scripts. The package remains dependency-free. Test tools are
private and locked in `packaging/sdk`. npm scope ownership and trusted-publisher
configuration belong to the maintainer's account. See the
[SDK release guide](../docs/how-to/release-sdk.md).

## Indexed native mosaics and explicit broad-view snapshots

An opt-in advanced layer budget enables persisted SQLite RTree extent lookup
and indexed exact observation attributes. Registration atomically replaces the
header, footprints, attributes and observations. Rendering reads one database
snapshot and loads a bounded ordered candidate set. It rejects exhausted source,
read, request-buffer or uncompressed-source estimates before raster execution.
Existing worker queues, admission and per-channel first/last-valid overlap remain
shared with ordinary native layers. SQL/Terracotta dataset routes retain their
selection semantics.

Collection-level products are explicit immutable layers. The local preparation
helper aligns and calibrates bands, evaluates expressions per observation,
composites in observation order, then averages valid computed samples or selects
the center sample. Category averaging is rejected. Parent/definition/output
checksums and block-level contribution counts accompany each product. Freshness
checks and regeneration happen outside request handling; applications deliberately
select the detailed or broad layer. This avoids silently changing scientific
aggregation after budget exhaustion. See [mosaic operations](../docs/how-to/mosaics.md).

## Native discovery and explicit map grids

Native `/tilesets` discovery returns opaque single-component IDs and URLs across
SQL, keyed native, STAC and advanced-layer projections. IDs encode the ordered
key tuple and survive WSGI decoded paths, including keys with literal slashes.
TileJSON 3.0 supplies absolute Mercator templates; PUBLIC_URL owns HTTPS/prefix
configuration without trusting forwarded headers. Geographic and RCFP polar
grids use explicit matrix definitions with x/y axes and top-left row order.
Array and image caches include matrix identity. No WMTS or OGC API service
conformance is inferred from these descriptors.

Point reads execute in the bounded raster pool on the base source affine. They
reuse native selected-band validity, then apply declared calibration and typed
expression semantics independently of display rendering. Public provenance omits
source locations and credentials. See [map services](../docs/reference/native-services.md)
and `tests/native_services.py` for the supported grids and qualification scope.

## Host Python callbacks over the shared native core

Trusted Python hosts can register scalar compute layers over one explicit native
observation. Keep callback execution in the bounded Python host pool; native
workers only read/resample inputs. Reuse the native scalar renderer for returned
values and keep raster, computed and image cache budgets separate. Model versions
and declared external revisions determine derived identity, never Python function
identity. Source mutation during a model cannot publish its result. Undeclared
dependencies disable derived caching. Standalone Rust and compatibility adapters
retain their current boundaries. See [Python computation](python-compute.md).
