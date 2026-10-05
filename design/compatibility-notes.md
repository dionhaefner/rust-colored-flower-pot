# Compatibility and native boundary notes

This is the distilled maintainer record of behavior that is easy to accidentally
change. Check the current implementation and pinned reference before altering it.
Public scope is in [compatibility](../docs/compatibility.md) and
[native differences](../docs/native-migration.md). Preserve observable contracts;
propose corrections as explicit behavior changes instead of silently cleaning up
legacy quirks.

Completed local checks, unresolved correctness failures, retained scope decisions,
and pending qualification are recorded in the [validation handoff](human-validation.md).

The quirks below describe the Terracotta profile. Native corrections to ingest,
percentile labels, empty dimensions, extrema, rotated hulls, and HTTP validation
are documented in [native migration](../docs/native-migration.md). Keep each
profile's regression assertions distinct.

## Catalog and query semantics

- Stored bounds columns have misleading north/east/south/west names. Preserve
  their positional mapping to west/south/east/north when reading and writing.
- Percentiles are raw native-endian float32 blobs without a byte-order marker.
  Preserve precision and byte layout. Cross-endian legacy catalogs are not
  qualified; portable encoding needs an explicit new format.
- Backend collation affects dataset identity, matching, and ordering. SQL owns
  pagination and ordering ties; adding a tie-breaker changes returned datasets.
  An empty filter list retains the legacy no-op predicate behavior.
- Skip-metadata overwrite can retain the previous metadata row. Legacy ingest
  and surrounding Python connection contexts commit successful operations
  individually. Use explicit native batches when atomicity is required.
- MySQL reflection yields ten-decimal `Decimal` values when reopening a catalog.
  Default NumPy stretches can fail; explicit complete numeric/percentile stretches
  bypass that failure. Keep Python types and HTTP numeric conversion distinct.
- SQLite/PostgreSQL historical migrations are transactional. MySQL DDL can
  partially commit and the historical 0.7 MySQL chain is unsupported. Publish
  format markers only after statements succeed; restore backups before retrying.
- Older reference notes rejected all MySQL query options because the oracle
  appends a malformed charset suffix. That blanket restriction is superseded:
  current RCFP supports the documented verified-TLS/private-CA and explicit
  loopback options. Do not remove these security controls to mimic the old bug.

Relevant code: `src/storage.rs`, `src/storage/relational.rs`,
`src/storage/migrations.rs`, `src/python/driver.rs`, and Python provider wrappers.
Maintained checks: Rust storage tests, `tests/storage_contracts.py`,
`tests/compatibility_contracts.py`, and `tests/compatibility_sql.py`.

## Rendering, masks, and expressions

- Percentile stretch labels use zero-based array indexing: `p0` selects the first
  stored percentile, `p98` the last, and `p99` is rejected for 99 entries. Preserve
  this when reproducing the named profile's image semantics.
- Out-of-bounds tiles are transparent PNGs at `DEFAULT_TILE_SIZE`, even when
  requested dimensions differ. Preserve status and dimensions together.
- Singleband zero values and RGB black-key transparency are representation
  rules. Partial RGB masks remain channel-local: a masked red channel must not
  erase nonzero valid green/blue. Hidden masked values influence chained
  gamma/sigmoidal/LCH operations, so operation order and hidden data matter.
- Compute retains NumPy source dtypes, integer wrap/promotion, comparison and
  bitwise behavior, mask propagation, and negative-integer-power errors. Converting
  everything to float changes ordinary index imagery materially.
- Reprojection and read resampling are separate operations. Preserve padded VRT
  orchestration, overview selection, categorical nearest sampling, source-derived
  preview bounds, and longitude wrapping. Dateline/polar previews and tile edges
  require tests beyond an ordinary opaque projected tile.
- Optimization reads 256×256 windows; a whole-image translate can change output
  despite matching grid and masks. Preserve `UNIFIED_SRC_NODATA=YES`, the
  bounds-based suggested grid for rotated sources, first-band selection, dataset
  masks, overview methods, and final compressed-copy behavior.
- Chunked statistics reproduce the pinned Crick maximum initialization
  `-DBL_MIN` for entirely negative inputs; full/sample statistics use the real
  maximum. Changing it is a statistical contract change, not harmless refactoring.
- Legacy HTTP quirks include character iteration of bare `order_by`, internal
  errors for empty/unknown metadata columns, and a specification omitting the
  supported POST metadata route. The advertised `mod` arity does not match its
  evaluator. Reversed stretches differ between singleband, RGB, and compute.
  Reproduce with the reference before changing status, sorting, or validation.

Relevant code: `src/raster/`, `src/statistics.rs`, `src/expressions.rs`,
`src/image.rs` and `src/color.rs`. Internal Python image/expression helpers are
outside the documented compatibility API. Retained
expression golden vectors are in `tests/fixtures/expressions.json` and
`expression-types.json`; real raster provenance is in `tests/fixtures/corpus/`.

## Narrow accepted numerical differences

Compressed PNG bytes need not match. Image dimensions, geographic placement,
coverage, masks, classes, and meaningful HTTP/Python contracts remain independent
checks. Do not widen a general tolerance to hide an unexplained difference.

| Operation/input                                                                     | Recorded acceptance boundary                                                                                                                                                       |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Float32 transcendental expression vectors                                           | Two float32 machine epsilons relative to magnitude; other arithmetic/dtypes/masks retain their checks                                                                              |
| Pinned DEM full/sample mean and standard deviation                                  | Four float32 ULPs; stored percentiles, ranges, validity, masks, and geometry checked separately                                                                                    |
| Pinned 17,280,000-valid-sample dateline mosaic                                      | Mean within 0.000005 m and standard deviation within 0.00021 m; this input-specific exception does not replace small-crop bounds                                                   |
| Rotated/chunk robust hull case                                                      | Relative symmetric-difference area within 1e-9; other recorded geometries retain 1e-10                                                                                             |
| Opt-in native UTM interior sampler on the measured EPSG:32632 zoom-13 UInt16 corpus | Exact alpha masks; per-image p99 RGB channel difference at most 4/255 and greatest per-image mean absolute RGB difference about 0.61/255, with ordinary-scale tile/seam inspection |

These are acceptance records for particular inputs and operations, not blanket
runtime guarantees or a universal pixel threshold. The UTM strict decoded-hash
comparison did fail; its visual acceptance is separate, and compatibility mode
disables the path. A different CRS, dtype, edge, zoom, or workload needs fresh
evidence. Small numerical differences must be explained and visually immaterial;
dataset selection, coverage, category meaning, and public workflow failures are
material even if a global image score looks good.

Historical comparison inputs and detailed reports remain in the local archive
identified in [maintainer context](README.md). Use maintained real fixtures and
the pinned oracle for new checks; archive acceptance records alone do not qualify
new artifacts.

The broader local [raster matrix](human-validation.md#raster-correctness)
passes all 3,646 cases on the recorded Linux shared core. General-reader grids
use the source bounding rectangle for suggested resolution and pass the padded
affine directly to GDAL; read resampling retains the source dtype before widening.
Source nodata still masks interpolated output when the VRT has alpha and no
destination nodata. These corrections restore exact raster/image comparisons;
they introduce no additional numerical acceptance boundary. Hosted platforms,
remote providers and opt-in samplers retain their separate qualification limits.

## Native lifetimes and Python boundaries

GDAL initialization is serialized, its process-lifetime library stays loaded,
and application teardown joins worker threads before destroying owned state.
Initialization locking alone does not prevent thread-local destructor crashes.
Response deadlines and disconnects do not cancel native work.

Do not fork live GDAL state. The documented driver facade wraps native raster
operations with a thread executor and NumPy arrays/Futures; it no longer maintains
a separate Python raster process implementation. Settings/profile snapshots are
carried into operations; request handling must not rewrite process environment
variables. Persistent WSGI sessions keep native ownership alive across requests.
Native profiling gives request diagnostics; internal Python profiler/debugger
helpers are outside the supported API.

Remote SQLite refresh uses validated immutable generations and leases. Preserve
the old generation for an outer Python connection context; fresh connections may
observe a published replacement. Read-only/lazy metadata failures remain named
public exceptions instead of attempted remote writes.

Compatibility SQL driver caching distinguishes complete connection URLs, including
credentials and TLS options. Reopening the same catalog with different connection
options must not silently inherit an existing session. Canonical local SQLite
paths still share a driver; public SQL catalog identities remain credential-free.

The maintained public replay selects documented driver workflows. Internal image,
expression, schema, backend-hook, Flask and Click tests are outside that gate;
SQL providers have a separate live roundtrip lane. Earlier broad replay reports
retained their four explicit exclusions and describe their original artifacts.
Reference ANSI capture failures and Python boto mocks are classified separately
from product passes. See [reference gates](../tests/reference/README.md).

## Native tile performance contract

`NATIVE_DIRECT_READ` defaults true in native mode; the named Terracotta profile
disables the requested-grid sampler regardless of that setting. The new path
uses GDAL/PROJ across CRSs and scalar dtypes, nearest for categories and the
configured reprojection resampler for continuous tiles. It can change samples
relative to the padded intermediate reader. Native independent grid/coverage/
quality gates cover its declared test matrix; exact Terracotta pixels are not
its contract. Previews retain the existing reader and their broader qualification
limits. `TC_NATIVE_DIRECT_READ=false` provides a general-reader control.

Positive explicit alpha is tested in floating precision, including alpha below
1; nodata/nonfinite values remain invalid. Map-grid float preparation without
nodata/alpha uses NaN padding to preserve valid zeros. Square/rectangular output,
partial coverage, and category identity have separate checks. Fusion retains
normalization order and ties exactly and falls back for color transforms.

Native tiles accept one-based TIFF band selectors; catalog metadata is still
band 1, so nondefault bands require explicit numeric stretches. Native RGB
`format=webp&quality=...` is an explicit lossy display option, with the same binary
coverage and image/webp MIME. PNG is the default. Both formats work through native
HTTP/Python/WSGI/Lambda response boundaries. The legacy specification excludes
these native additions; reference specification comparisons classify only the
listed native additions, not arbitrary drift. Run native exact-pixel reference
controls with `TC_NATIVE_DIRECT_READ=false`; direct native sampling uses its
independent GDAL quality gate instead.

Encoded images key source file generations, metadata/stretch/grid/style/bands/
format. SQLite descriptors invalidate on data_version and own writes; no
transaction/rollback data is retained. Main file and external mask/overview/aux
stamps invalidate local source handles and arrays. Remote handle/image retention
requires explicit TTLs; no claim of immediate mutable-URL/credential refresh is
made from handle TTL alone. Coalescing errors/panics release followers. Queued
abandoned work may be skipped, while active GDAL work remains accounted.
