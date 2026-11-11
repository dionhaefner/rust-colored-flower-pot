# Implementation constraints for contributors

Read the [architecture](explanation/architecture.md), [native differences](native-migration.md)
and [compatibility contract](compatibility.md) before changing shared behavior.
The [test guide](https://github.com/dionhaefner/rust-colored-flower-pot/blob/main/tests/README.md)
maps each interface to its maintained checks.

## Ownership and adapter boundaries

The Rust core owns catalog queries, metadata, expressions, raster reads and HTTP
response semantics. Python owns NumPy/Future representation and trusted compute
callbacks. Transport adapters parse requests and carry immutable settings into
the shared application. The CLI launcher locates its executable without importing
the Python raster API. Keep these boundaries intact when adding entry points.

An application owns bounded queues, workers, caches and source leases. GDAL handles,
VRTs and projection plans belong to one worker. Initialize GDAL under its global
lock and keep its library mapped for the process lifetime, including thread-local
destructors. Drop projection plans before their root datasets. Response deadlines
and disconnects abandon queued work but do not cancel executing GDAL calls.
Shutdown closes admission, drains work and joins workers. Python destruction
releases the GIL while joining. Embedded applications leave signal handling to
their host. Never fork live native or Python worker state.

Active identical renders coalesce even with zero image retention. Keep accounting
until work actually finishes, wake followers on errors/panics and keep foreground
admission bounded. Prefetch runs only while foreground work is idle. Caches have
separate source, array, descriptor and encoded-image budgets and freshness rules.
Construct persistent applications rather than creating a pool per request.

## Catalog formats and identities

Keep package version, compatibility profile, HTTP protocol and catalog format
distinct. Hatch VCS supplies the package version. The `terracotta` profile targets
Terracotta 0.10.1 and format `0.10`. Native keyed catalogs use `rcfp-native-2`;
advanced layers use `rcfp-native-1`. A package release does not by itself change
any catalog marker. Resolve profiles before application creation and Python import.

SQL engines own filtering, collation, ordering and pagination. Preserve these
details in Terracotta catalogs:

- Bounds columns have misleading names. Their positional mapping is west, south,
  east, north.
- Percentile blobs contain native-endian float32 values without a byte-order
  marker. Cross-endian catalog interchange is unqualified.
- An empty filter list is a no-op. Adding a sorting tie-breaker changes results.
- Overwriting with skipped metadata can retain the old metadata row. Compatibility
  connection contexts commit individual successful operations. Native batches and
  native CLI ingestion provide explicit atomic writes.
- Reopened MySQL metadata can contain ten-decimal `Decimal` values. Keep Python
  representations distinct from HTTP numeric conversion.
- SQLite/PostgreSQL migrations are transactional. MySQL DDL can partially commit;
  the historical 0.7 MySQL migration chain is unsupported. Publish markers last.

Remote SQLite uses read-only whole-file TTL snapshots. Validate a private new
generation before publishing it and retain old generations while readers hold
leases. Refresh failure must permit a later retry. Resolve AWS credentials on
refresh. SQL session cache keys include a private hash of the complete connection
URL so credentials and TLS options cannot accidentally share sessions. Public
identities exclude credentials. Verified TLS is the default; plaintext is limited
to explicit literal-loopback configurations.

Native keyed definitions persist selected physical bands, calibration,
expressions, display defaults and resolved STAC snapshots in SQLite. Band validity
combines finite samples, the selected mask, nodata and positive alpha before
alignment. Apply scale/offset afterward in f64. Categories require raw values and
nearest sampling. Expression statistics are sampled; their extent polygon is not
an exact valid-data hull. Reject missing dependencies and cycles before writes.
Rebinding invalidates dependent plans, statistics and image identities.

## Rendering contracts

The `terracotta` profile preserves reference behavior, including these quirks:

- Percentile labels index the stored array from zero. With 99 entries, `p98` is
  last and `p99` is rejected.
- Out-of-bounds tiles use `DEFAULT_TILE_SIZE` even when a different size is
  requested. Check status, dimensions and coverage together.
- RGB masks are channel-local. Hidden masked values affect chained color
  operations. Preserve transformation order and black-key transparency.
- Expressions retain NumPy source dtypes, integer promotion/wrap, comparisons,
  bitwise operations and negative-integer-power errors.
- Reprojection and read resampling are distinct. Preserve padded VRT geometry,
  overview selection, source dtype and longitude wrapping.
- Optimization reads 256×256 windows and preserves unified nodata, first-band
  selection, masks, overview methods and the final compressed copy.
- Chunked statistics retain Crick's `-DBL_MIN` maximum initialization for wholly
  negative inputs. Full/sample statistics use the actual maximum.
- HTTP error shapes, metadata ordering and reversed stretches have reference
  quirks. Reproduce them before changing validation or status codes.

Native map tiles read into the requested grid. Contained Web Mercator reads use
floating RasterIO; other geometries use a requested-grid warped VRT. This can
select different samples from the compatibility reader. Previews use the general
reader. The native UTM sampler is separately opt-in and disabled in the
compatibility profile. Preserve category, mask, seam and geographic-placement
checks independently of aggregate image scores.

Async-TIFF handles eligible header reads and exact stored-pixel windows on
contained EPSG:3857 grids. Sidecars, unsupported codecs/layouts, projection and
resampling fall back to GDAL. The compatibility profile always uses GDAL.
`TC_ASYNC_TIFF=false` supplies a control. TIFF windows execute serially per worker.
Broaden fast paths only with independent format, dtype, mask, overview and
projection evidence. Positive alpha, including values below one, is tested in f64.

PNG compression bytes may differ while decoded pixels match. Numerical bounds
are fixture-specific: float32 transcendental vectors allow two float32 epsilons
relative to magnitude; the pinned DEM mean/stdev allow four float32 ULPs. The
large dateline mosaic and rotated hull cases have their own assertions. Do not
turn those exceptions into a global tolerance or regenerate goldens from RCFP.

## Discovery, publication and Python computation

STAC discovery completes bounded pagination or static traversal before listening.
Remote document links stay on one origin; local metadata stays local. Credentials
must not follow links across origins. Direct asset access verifies SHA-256 while
staging the whole self-contained TIFF on first use. Preserve cumulative staging
budgets and selection lifetime until workers drain. A TTL, ETag or signed URL
alone does not establish immutable content. Stored selections retain resolved
Items and absolute asset locations, with credential generation outside the catalog.
Applications retain at most 64 stored source generations; further changes need a
restart. Public descriptors omit credentials, raw URLs and arbitrary properties.

Rendered revisions include canonical parameters, source/definition generations,
effective settings and encoded bytes. Check revision mismatches before HTTP
conditions. A revision guard does not archive source snapshots or purge external
caches. Authorization/cookie requests and errors use no-store; shared publication
requires an explicit setting. Conditional requests validate the application's
visible representation, not an immediate upstream refresh. Unix source stamps
include inode/change time; other platforms retain size/mtime detection.

Python compute callbacks run only in a trusted host after native input alignment.
Inputs are immutable binary arrays with independent validity masks. Keep admission
permits until queued/running work leaves the executor, including caller timeouts.
Compute identity includes model, parameters, source and declared external revisions.
Missing dependency declarations or unrevisioned remote inputs disable derived
retention and callback sharing. Never persist code or accept callbacks over HTTP.
Computed responses use no-store and content-qualified ETags. See the
[compute contract](how-to/python-compute.md) for supported scalar output and limits.

## Packaging and verification

Wheels carry the executable, shared core, limited-API extension, GDAL/PROJ,
browser assets and licenses. Linux OpenSSL libraries use private SONAMEs and
dependency references so Python's existing OpenSSL cannot satisfy their imports.
Test both import orders with `hashlib` and `ssl`. Preserve this isolation in
source builds as well as repaired release wheels.

Keep Hatch VCS and the generated version file in source archives. Core metadata
version 2.4 is explicit. Qualify final repaired artifacts, including RECORD,
entry points, native dependency closure and platform tags. A source-archive build
must work without checkout-only context. Lambda has its own AL2023 libc baseline.
CI configuration is not evidence that a platform or deployment passed.

Use checksum-pinned real inputs and independent Terracotta/Rasterio expectations.
Record artifact/source hashes, native versions, settings with secrets redacted,
commands, failures, exclusions and unavailable checks in fresh ignored output
directories. Golden outputs come from the oracle. Keep repeatable assertions in
`tests/`, supported build helpers in `packaging/`, and current contributor/operator
guidance in `docs/`. Release qualification belongs beside the exact artifacts.
