# Rust rewrite discovery log

Follow the policy in [REWRITE.md](REWRITE.md). This is the running record of
legacy bugs, issues, inconsistencies, missed opportunities, compatibility traps,
and accepted differences found during the rewrite. It is not an instruction to
fix them all in the initial port.

Use stable IDs (`RW-001`, etc.). Add entries when findings arise, update them as
evidence improves, and retain resolved entries with links to the fix or decision.
Link richer test results or design notes instead of copying large artifacts.

For each entry record:

- **Kind:** legacy bug, inconsistency, compatibility constraint, opportunity,
  accepted deviation, or port regression.
- **Evidence:** source and baseline version; reproduction or fixture when
  available. Distinguish suspected from reproduced behavior.
- **Impact:** what an application user or integrator would observe.
- **Initial-port treatment:** preserve, reproduce and investigate, accept a
  bounded difference, or fix a regression introduced by the port.
- **Follow-up/status:** next action, target phase, and eventual resolution.

An accepted numerical deviation must explain its cause and ordinary-viewing
impact, link comparison evidence, and identify its regression check. A suspicion
or a failed comparison alone is not evidence that a difference is acceptable.

## Initial findings

The entries below were identified by source inspection at `6db38d5`. They are
not runtime reproductions, and no rewrite deviations have yet been validated.

### RW-001: Bounds column names disagree with stored coordinate order

- **Kind:** Inconsistency / compatibility constraint.
- **Evidence:** [`RelationalMetaStore._encode_data` and `_decode_data`](../terracotta/terracotta/drivers/relational_meta_store.py)
  store bounds positions 0–3 under north/east/south/west names, while
  [`raster.compute_metadata`](../terracotta/terracotta/raster.py) obtains west/south/east/north
  bounds. Encoding and decoding use the same positional mapping.
- **Impact:** A new reader that trusts the column names misinterprets existing
  data and can place imagery incorrectly.
- **Initial-port treatment:** Preserve the stored mapping at the compatibility
  boundary; use clear internal coordinate names.
- **Follow-up/status:** The P01 read path preserves the positional mapping and
  the retained metadata differential matches exactly. Write and cross-language
  round trips remain P05/P06 work; schema cleanup still requires a later migration.

### RW-002: Percentile labels appear inconsistent with array indexing

- **Kind:** Suspected legacy bug.
- **Evidence:** [`compute_image_stats`](../terracotta/terracotta/raster.py) produces percentiles
  1 through 99; [`get_stretch_scale`](../terracotta/terracotta/image.py) returns
  `percentiles[N]` for `pN`, accepting indices 0 through 98.
- **Impact:** Requested percentile labels may resolve to unexpected values or
  reject advertised endpoints, affecting stretches.
- **Initial-port treatment:** Capture actual boundary and typical behavior in
  fixtures and preserve it. Do not silently correct indexing during the port.
- **Follow-up/status:** Confirmed at runtime in P00; see RW-011 and the retained
  `percentile-p98` / `percentile-p99` captures. Evaluate corrected semantics in
  a subsequent version.

### RW-003: Application versions double as compatibility identifiers

- **Kind:** Compatibility constraint.
- **Evidence:** [`RelationalMetaStore._connection_callback`](../terracotta/terracotta/drivers/relational_meta_store.py)
  and [`connect`](../terracotta/terracotta/scripts/connect.py) compare major/minor application
  versions for database and server compatibility respectively.
- **Impact:** A new rewrite version can reject unchanged databases or cause old
  clients to reject a compatible server.
- **Initial-port treatment:** Separate internal release identity from legacy
  compatibility identifiers; test old-client and database acceptance.
- **Follow-up/status:** P03 now exposes separate implementation (`0.1.0`),
  protocol (`0.10.1`), and database (`0.10`) identities through CLI JSON,
  native HTTP and Python. The legacy values remain accepted/emitted. P04
  verifies unchanged `terracotta connect` against native `/keys` and
  `/swagger.json`; see [client record](validation/evidence/p04/http/old-client.json).
  A future capability/format design must retain its transition path.

### RW-004: Out-of-bounds response dimensions ignore request dimensions

- **Kind:** Legacy inconsistency.
- **Evidence:** [`handle_tile_out_of_bounds_error`](../terracotta/terracotta/server/flask_api.py)
  constructs a transparent PNG with `DEFAULT_TILE_SIZE`.
- **Impact:** A request with custom dimensions can receive a default-sized empty
  tile. Replacing this response with a 404 also changes existing behavior.
- **Initial-port treatment:** Preserve the response behavior and capture a
  custom-size out-of-bounds fixture.
- **Follow-up/status:** Reproduced by P00 and preserved exactly by P01, including
  a `[128,512]` request returning the default 256 × 256 transparent image.
  Consider consistent requested dimensions only in a later compatibility break.

### RW-005: PNG transparency has representation-specific behavior

- **Kind:** Compatibility constraint.
- **Evidence:** [`array_to_png`](../terracotta/terracotta/image.py) fills masked values with
  zero, uses index/value zero for singleband transparency, and uses black as
  the RGB transparency key.
- **Impact:** Combining per-band masks into a new alpha mask can change the
  appearance of partially masked RGB pixels.
- **Initial-port treatment:** Compare decoded color and transparency for partial
  masks; preserve observable behavior rather than assuming a new RGBA encoding
  is equivalent.
- **Follow-up/status:** Preserved and verified by P02/P04 real-raster partial
  mask and chained-transform comparisons (RW-014/RW-017). Evaluate semantic
  changes separately from PNG encoder choice in a subsequent version.

### RW-006: Metadata percentile encoding has implicit byte-order assumptions

- **Kind:** Compatibility constraint.
- **Evidence:** [`_encode_data` and `_decode_data`](../terracotta/terracotta/drivers/relational_meta_store.py)
  serialize percentiles as raw NumPy `float32` bytes without an explicit
  byte-order marker.
- **Impact:** Misreading byte order or changing precision breaks metadata and
  percentile-based rendering for existing databases.
- **Initial-port treatment:** Establish the reference platform's encoding and
  preserve it; test real blob round trips. Document any unsupported legacy
  platform encoding rather than guessing.
- **Follow-up/status:** P01 reads the retained native-endian float32 blob exactly
  and reproduces metadata and percentile rendering. Cross-endian qualification
  remains open; explicit portable encoding would require a later format decision.

### RW-007: Remote SQLite refresh overwrites the current snapshot

- **Kind:** Potential concurrency issue / opportunity.
- **Evidence:** [`_update_from_s3`](../terracotta/terracotta/drivers/sqlite_remote_meta_store.py)
  opens the local cached database with `wb` and copies the download into it.
  Concurrent-reader failure has not been reproduced.
- **Impact:** A more concurrent server could expose readers to an incomplete
  snapshot or inconsistent connection state during refresh.
- **Initial-port treatment:** Retain read-only, whole-download, TTL behavior;
  implement safe snapshot publication and retire old connections deliberately.
  This internal improvement need not reproduce an overwrite race.
- **Follow-up/status:** Addressed in P06 with private immutable generations and
  leases. Concurrent/failed refresh and old-reader retirement pass; see
  [P06 remote evidence](validation/evidence/p06/README.md). The legacy race itself
  remains an inspected risk, not a reproduced oracle failure.

### RW-008: Raster tests do not establish full visual equivalence

- **Kind:** Validation gap.
- **Evidence:** [`test_get_raster_tile`](../terracotta/tests/test_raster.py) parametrizes
  resampling and value preservation but asserts only output shape. Other tests
  provide additional coverage; this finding concerns that core path.
- **Impact:** A port can pass this check while changing tile contents or masks.
- **Initial-port treatment:** Add differential fixtures for pixels, masks,
  boundaries, and representative application views before claiming parity.
- **Follow-up/status:** P01 adds exact decoded-RGBA differentials for its
  single-band slice across real projections, nodata/internal masks, categorical
  data, adjacent seams, dimensions and empty output, plus a native-scale visual
  sheet. P02/P04 extend exact image checks to RGB, compute, all palettes,
  transforms and 16 read/warp method pairs; see
  [P04 evidence](validation/evidence/p04/README.md). Broader release workloads
  remain P08.

### RW-009: Source wheel omits the API documentation template

- **Kind:** Legacy packaging bug.
- **Evidence:** At frozen commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`,
  `pip install` builds a wheel whose `terracotta/server/templates/apidoc.html`
  is absent. The unchanged `test_get_spec` then receives 500 for `/apidoc` with
  `jinja2.exceptions.TemplateNotFound`. The isolated editable source install
  passes the same test. See `validation/evidence/p00/upstream-local.log` and
  `upstream-final.log`.
- **Impact:** A wheel installation can serve `/swagger.json` but fail the
  browser API documentation endpoint.
- **Initial-port treatment:** Capture from the unchanged isolated source tree and
  preserve the endpoint contract. Do not reproduce the packaging omission.
- **Follow-up/status:** Reproduced in P00; P04 embeds and audits documentation
  and static assets. Packaged Python/WSGI/Lambda-event docs/spec requests pass
  exactly; see [adapter report](validation/evidence/p04/adapters/report.json).

### RW-010: Color-log tests depend on the ambient `NO_COLOR` variable

- **Kind:** Test/environment inconsistency.
- **Evidence:** The execution environment exports `NO_COLOR=1`. Colorlog honors
  it, while `tests/scripts/test_cli.py::test_logging_visible` unconditionally
  requires ANSI reset codes. The unchanged suite gives 476 passes, 54 skips and
  two color failures with that variable; `env -u NO_COLOR` gives 479 passes and
  54 skips. See `validation/evidence/p00/upstream-local.log` and
  `upstream-final.log`.
- **Impact:** The baseline test can fail even though intentional no-color output
  is readable and command behavior is otherwise unchanged.
- **Initial-port treatment:** Record the environment and capture the oracle with
  its normal application settings. Preserve meaningful logging configuration;
  terminal ANSI byte identity is outside the compatibility policy.
- **Follow-up/status:** Reproduced in P00. P07 should specify color behavior when
  inventorying CLI/logging contracts.

### RW-011: Percentile names use zero-based indexes

- **Kind:** Reproduced legacy bug / compatibility constraint.
- **Evidence:** P00 reference capture `percentile-p98` succeeds while
  `percentile-p99` returns 400 for a stored 99-value percentile array. Source
  `image.get_stretch_scale` indexes `percentiles[N]`, confirming RW-002 at
  runtime. See `validation/reference/capture/capture.json`.
- **Impact:** `pN` selects the `(N+1)`th stored percentile and the advertised
  `p99` endpoint is rejected.
- **Initial-port treatment:** Preserve this behavior in the initial port and
  retain the differential cases.
- **Follow-up/status:** Reproduced in P00 and preserved by the P01 `p98`/`p99`
  differential. Keep the boundary cases when completing rendering in P04.

### RW-012: Rasterio's private GDAL runtime needs its sibling library directory

- **Kind:** Packaging constraint.
- **Evidence:** P01 inspected the pinned Rasterio wheel's
  `libgdal-rasterio-*.so.35.3.9.3`: it has hashed `NEEDED` dependencies such as
  `libcurl-rasterio-*.so` but no RUNPATH. Direct loading fails unless
  `rasterio.libs` is in the dynamic-loader search path. The P01 executable
  re-executes itself with that directory when using the P00 development venv.
- **Impact:** Copying only the GDAL shared object, or launching the P01 binary
  outside this workspace without a system/packaged runtime, will fail before
  serving requests.
- **Initial-port treatment:** Keep the P00 venv discovery as a local development
  fallback. Do not present it as distribution support.
- **Follow-up/status:** Resolved for P03's Linux x86_64 artifacts. The
  relocatable host bundle includes the complete Rasterio native directory plus
  GDAL/PROJ data and startup discovery relative to the executable. Python uses
  an extension RPATH; the container installs declared base libraries; Lambda
  bundles AL2023 expat/libgcc/libstdc++/zlib. Host, Debian container, and pinned
  `provided.al2023` image real-raster loads pass without reference-venv runtime
  discovery. Other operating systems/architectures remain C32 work.

### RW-013: GDAL worker runtime initialization must be serialized

- **Kind:** Native-library concurrency constraint / resolved port regression.
- **Evidence:** During P02, starting three threads that each dynamically loaded
  Rasterio's private GDAL 3.9.3 and called `GDALAllRegister` concurrently caused
  the Rust server to exit with `SIGSEGV` before binding. Starting each worker and
  waiting for its GDAL-ready result before starting the next eliminated the
  crash. The workers still execute raster jobs concurrently after initialization;
  retained P02 fast/failure/stress runs all passed with peak native concurrency
  at the configured bound.
- **Impact:** Concurrent process-global GDAL initialization can crash startup;
  request-time concurrency itself remains supported.
- **Initial-port treatment:** Serialize dynamic loading/registration during
  worker startup and fail startup if any worker cannot initialize. Do not call
  process-global registration concurrently.
- **Follow-up/status:** Fixed in P02 `RasterExecutor::start` and rechecked in P03
  against the host bundle, clean Debian container, and AL2023 Lambda package;
  serialized startup and concurrent request reads remain stable.

### RW-014: RGB partial masks retain channel-local data and black-key transparency

- **Kind:** Reproduced compatibility constraint.
- **Evidence:** P02 generated a narrow derivative from the real Sentinel-2 red
  subset with a red-only internal-mask window, leaving green and blue unchanged.
  The frozen Python and Rust decoded RGBA were exact. Pixels with masked red and
  valid nonzero green/blue remained opaque; only fully black RGB pixels use the
  PNG transparency key. See `validation/evidence/p02/fast/report.json` and
  `side-by-side.png`.
- **Impact:** Combining band masks into one alpha plane would incorrectly erase
  valid color data from partially masked pixels.
- **Initial-port treatment:** Preserve independent channel masks through
  per-band zero fill and encode legacy `(0,0,0)` RGB transparency.
- **Follow-up/status:** P02 resolves the RGB portion of RW-005. Keep this fixture
  when P04 adds color transforms and the remaining RGB option matrix.

### RW-015: Host-built Lambda executable exceeded target glibc

- **Kind:** Packaging constraint / resolved port regression.
- **Evidence:** The first P03 release binary built on Ubuntu glibc 2.39 required
  `GLIBC_2.35` and failed immediately in the pinned official
  `provided.al2023` image (`GLIBC_2.35 not found`), whose runtime has glibc
  2.34. The exact failure and corrected image evidence are retained in the P03
  evidence record.
- **Impact:** Local Lambda event tests could pass while the uploaded custom
  runtime cannot start in AWS.
- **Initial-port treatment:** Build the Lambda executable and cdylib inside the
  pinned AL2023 target image; bundle its non-base native dependencies and
  notices. The final executable requires at most glibc 2.34 and renders the
  pinned real raster exactly in that official image.
- **Follow-up/status:** Resolved for Linux x86_64 `provided.al2023` local-runtime
  compatibility. An actual AWS service invocation remains not run because no
  cloud deployment/spend was authorized; arm64 requires its own target build.

### RW-016: Compute arithmetic retains NumPy source dtypes

- **Kind:** Compatibility constraint / resolved port regression.
- **Evidence:** The frozen reference reads native uint16 Sentinel-2 arrays.
  `(v1-v2)/(v1+v2)` wraps unsigned subtraction/addition before division;
  negative integer powers error and integer bitwise operators succeed.
  P04's first float-only evaluator changed 116,166 decoded NDVI channels.
  Retaining GDAL sample type and NumPy promotion/wrap rules fixes the real-image
  comparison. `validation/reference/p04-expression-types.json` retains additional
  byte, uint16, int16, float32 and float64 array/mask oracle vectors.
- **Impact:** Converting all compute operands to float silently changes ordinary
  index imagery. This is material, not acceptable floating-point noise.
- **Initial-port treatment:** Preserve source dtype arithmetic through evaluation.
- **Follow-up/status:** Fixed in P04; later Python APIs must preserve result dtype
  as well as the HTTP-visible values/masks.

### RW-017: Hidden masked channel values affect chained color operations

- **Kind:** Reproduced compatibility constraint / resolved port regression.
- **Evidence:** Frozen NumPy masked in-place stretching leaves hidden values
  unchanged before clipping; gamma retains masked hidden data; sigmoidal leaves
  hidden data at 1 (positive contrast) or contrast × bias (negative contrast).
  LCH saturation consumes these values while restoring channel-local masks.
  P04's partial-mask saturation and gamma/sigmoid/saturation cases initially
  differed by up to 95/77 channels levels and were repaired using those rules.
- **Impact:** Filling masked channels with zero or applying ordinary arithmetic
  beneath masks changes the other visible RGB channels at mask boundaries.
- **Initial-port treatment:** Preserve operation order and hidden data, extending
  RW-014's independent-channel transparency rule.
- **Follow-up/status:** P04 retains direct real-Sentinel partial-mask comparisons.

### RW-018: HTTP and expression reference quirks retained in P04

- **Kind:** Reproduced legacy inconsistencies.
- **Evidence:** P04 HTTP oracle: bare `order_by=band` is iterated as characters
  and returns 400; bracketed `[path]` sorts the stored path text, including
  relative/absolute distinctions. Empty/unknown metadata columns (including
  `keys`) raise internal errors. The API specification omits POST `/metadata`
  although the route exists. `mod` advertises one argument but NumPy requires
  two, so either arity fails. Singleband permits reversed stretch bounds while
  RGB rejects them and compute requires a strictly increasing range.
- **Impact:** Apparent cleanups would change status codes, sorting, expression
  behavior, or the frozen specification returned to existing clients.
- **Initial-port treatment:** Preserve these contracts; do not fix the oracle.
- **Follow-up/status:** P04 fixtures and HTTP matrix retain these cases. Corrected
  behavior is a future version decision.

### RW-019: Float32 transcendental implementations differ by final ULPs

- **Kind:** Accepted narrow numerical deviation (P04).
- **Evidence:** Pinned NumPy 2.2.6 vector math versus Rust/libm followed by
  float32 rounding: `exp(1)` yields 2.7182819843292236 versus 2.7182817459106445.
  Retained dtype vectors bound only float32 transcendental results by two
  float32 machine epsilons relative to magnitude; masks, arithmetic, other
  dtypes and categorical labels retain their independent checks.
- **Impact:** A final float32 ULP may differ. This is not permission for palette,
  mask, integer-overflow, or unexplained image differences.
- **Initial-port treatment:** Accept only the stated float32 transcendental bound;
  real DEM log/exp/sin decoded images remain exact.
- **Follow-up/status:** Accepted after all 361 expression vectors passed and real
  DEM log/exp/sin image pairs were inspected at native 256-pixel scale with no
  visible difference. See [P04 evidence](validation/evidence/p04/README.md) and
  [retained views](validation/evidence/p04/http/float32-and-chain.png).

### RW-020: GDAL library must outlive worker thread TLS destruction

- **Kind:** Native lifetime constraint / fixed port regression.
- **Evidence:** Repeated packaged P04 Python/WSGI calls, with the frozen Rasterio
  oracle also imported, reproducibly exited 139. GDB caught SIGSEGV on an exited
  `terracotta-gdal` worker inside `__nptl_deallocate_tsd`, calling unmapped code,
  while another worker was in `dlopen`. Worker-local `Library` ownership unloaded
  GDAL before pthread invoked the GDAL TLS destructor. Initialization locking
  alone (RW-013) did not address this lifetime.
- **Impact:** Short-lived application pools can crash at shutdown/recreation.
- **Initial-port treatment:** Keep a single process-lifetime GDAL library handle,
  register once under a process-wide lock, and retain worker-local dataset/VRT
  ownership. This bounds the library to one runtime and keeps TLS callbacks mapped.
- **Follow-up/status:** Fixed and verified in P04: all 46 packaged adapter checks
  pass, including 12 calls over four concurrent callers; see
  [adapter evidence](validation/evidence/p04/adapters/report.json). Keep this
  regression in `p04_adapters.py` for future pool/packaging changes.

### RW-021: Ingestion retains stale metadata and commits per insert

- **Kind:** inspected legacy quirks, P05 compatibility constraints.
- **Evidence:** frozen `relational_meta_store.insert` only replaces metadata when
  a metadata argument exists; skip-metadata overwrite retains the previous row.
  Nested `connect()` contexts each commit, so a later CLI failure does not undo
  preceding successful inserts. `scripts/ingest.py` constructs `click.Abort()`
  without raising it after a key-name mismatch, then inserts positionally.
- **Treatment:** retain these CLI behaviors. The native explicit batch API uses
  savepoints for atomic caller-requested batches; legacy Python connection-context
  semantics remain a separate P07 adapter concern. Differential evidence is P05.

### RW-022: Crick chunked maximum is wrong for entirely negative rasters

- **Kind:** legacy dependency bug / compatibility constraint.
- **Evidence:** pinned Crick 0.0.8 `stats_stubs.c` initializes maximum to
  `-DBL_MIN`, so updating only negative values never replaces that maximum.
- **Treatment:** preserve this value in chunked metadata, including the constant
  negative microfixture; full/sample statistics retain the actual maximum.
  Do not change the chunked quantile definition or silently repair the oracle.

### RW-023: Optimization requires the reference's window sequence and warp options

- **Kind:** repaired port regressions.
- **Evidence:** a single GDALTranslate of a warped VRT changed 805,847 real
  Sentinel pixels (maximum 2,289) despite identical grid/masks. The reference
  reads 256×256 windows; approximation and resampling depend on read windows.
  Multiband nodata also requires `UNIFIED_SRC_NODATA=YES`. Rotated sources use
  a grid suggested from axis-aligned `dataset.bounds`, then warp actual pixels.
- **Treatment:** preserve 256×256 data/mask reads, explicit nodata options and
  the bounds-based suggested grid. Preserve first-band output, dataset mask,
  reference overview factors/methods and final compressed TIFF copy.
- **Status:** repaired; base pixels, masks, all overviews and preview images are
  exact in P05 optimization/edge evidence, including rotated real-image derivatives.

### RW-024: Full/sample float32 reductions can differ by final ULPs

- **Kind:** accepted narrow numerical deviation.
- **Evidence:** real Copernicus DEM full mean: Python 1800.9281005859375,
  Rust 1800.927734375, difference 0.0003662109375 (three float32 ULPs).
  Both use float32 mean/std reductions; reduction scheduling/order differs.
  Integer and chunked double-precision moments retain separate tight checks.
- **Treatment:** bound only the DEM full/sample mean/std checks by four float32
  ULPs. Percentiles must match after the existing float32 database encoding;
  range, validity, masks and geometry have independent checks. Mean/std are not
  used by the legacy display stretch, and all 24 full/sample/chunk default and
  percentile-stretch image pairs are exact (72 status/image assertions).
- **Status:** accepted with retained raw metadata pairs and ordinary-size views;
  this is not a universal numerical or image tolerance. Raw native metadata uses
  the same float32 percentile boundary as SQLite; full legacy Python ndarray dtype
  and representation completeness remains P07.

### RW-025: Robust hull arithmetic and rotated-grid legacy behavior

- **Kind:** repaired port regression and accepted geometric roundoff.
- **Evidence:** a rotated real-Sentinel derivative exposed a relative hull-area
  mismatch of 0.000151 from plain floating-point orientation and affine evaluation
  order. Using GDAL/GEOS robust hull construction and Rasterio's per-window affine
  order reduces the mismatch to 2.4215e-10. Different robust geometry stacks can
  retain different nearly collinear vertices. The ordinary-scale boundary is
  indistinguishable; the retained check bounds this specific rotated/chunk case
  to 1e-9 relative symmetric-difference area. Other geometries remain at 1e-10.
- **Treatment:** use GEOS hulls; preserve the legacy all-valid chunk rectangle
  from `windows.bounds` even for rotated grids, and the full/sample axis-aligned
  bounds transform. No correction of those legacy geometric choices is implied.
- **Status:** P05 edge suite passes with the stated narrow rotated/chunk bound.

### RW-026: Relational collation is part of dataset identity and HTTP ordering

- **Kind:** inspected backend compatibility constraint, P06.
- **Evidence:** frozen `get_datasets` delegates WHERE and ORDER BY to SQL;
  MySQL uses utf8mb4 with the server's default collation. Rust's P04 in-memory
  case-sensitive filtering/sorting cannot preserve this on relational engines.
- **Treatment:** route SQL-backed filtering and ordering through the engine;
  keep bound values and quoted dynamic identifiers. Preserve the actual database
  collation instead of imposing a Rust comparator or a new schema collation.
- **Status:** Verified on MySQL 8.4.11 and PostgreSQL 16.10;
  [P06 engine evidence](validation/evidence/p06/engines.json).

### RW-027: MySQL historical DDL cannot provide whole-chain rollback

- **Kind:** inspected legacy engine constraint, P06.
- **Evidence:** frozen migration SQL uses CREATE/ALTER statements; MySQL DDL
  commits implicitly. Its 0.7 migration also uses a TEXT primary key and the
  unquoted `keys` table, unlike the supported contemporary MySQL schema.
- **Treatment:** preserve migration SQL and surface failures, never move a
  version marker before the corresponding statements succeed. SQLite/PostgreSQL
  run chains transactionally. Require a pre-migration copy for a MySQL retry
  after partial DDL; qualify actual failure behavior on disposable engines.
- **Status:** Verified on disposable historical copies;
  [P06 migrations](validation/evidence/p06/migrations.json).

### RW-028: MySQL reflection changes numeric representation and breaks default stretches

- **Kind:** reproduced legacy behavior, P06.
- **Evidence:** frozen SQLAlchemy reflects MySQL DOUBLE as `Decimal` rounded to
  ten decimal places. A newly created driver's cached schema uses float instead;
  reopening it exposes Decimals. HTTP Marshmallow converts those values back to
  JSON numbers. NumPy default singleband/RGB contrast stretching with Decimal
  endpoints raises `UFuncOutputCastingError`. Explicit numeric/percentile
  endpoints bypass it. Engine integration retains these cases and image pairs.
- **Treatment:** preserve ten-decimal HTTP metadata conversion and reopened
  MySQL default-stretch 500 behavior; explicit stretches retain real rendering.
  Native internal metadata remains binary64 and stored blobs remain native f32.
  Compare Python Decimal decoding explicitly at its existing ten-decimal boundary,
  never by widening general image/storage tolerances. Full Python object-type and
  newly-created connection-context fidelity remains P07.
- **Status:** Preserved and verified in P06 metadata/rendering checks;
  [surface matrix](validation/evidence/p06/surfaces.json).

### RW-029: Engine pagination ties and empty filters belong in SQL

- **Kind:** reproduced legacy query semantics; repaired P06 implementation.
- **Evidence:** MySQL may choose a different ordering among tied rows for
  `ORDER BY scene LIMIT ... OFFSET ...` than for an unlimited query. Applying
  pagination in Rust after fetching all rows changed the selected datasets.
  Frozen SQLAlchemy `or_()` with an empty list emits no predicate, so an empty
  filter list selects all rows for that field.
- **Treatment:** parameterize filters, preserve no-op empty lists, and execute
  sorting plus pagination on the engine. Do not invent a tie-breaker or normalize
  ordering out of comparisons. SQLite retains its corresponding semantics.
- **Status:** Verified in [P06 engine/HTTP comparisons](validation/evidence/p06/engines.json).

### RW-030: MySQL URL query options are broken by the legacy charset suffix

- **Kind:** inspected and reproduced legacy path bug, P06.
- **Evidence:** `MySQLMetaStore.__init__` unconditionally appends
  `?charset=utf8mb4`, including when the path already has a query. Supplying
  `ssl_ca=/path/ca.pem` makes the oracle try to read
  `/path/ca.pem?charset=utf8mb4`. Other supplied connection options similarly
  become malformed. Normal credential/host/port/database paths remain valid.
- **Treatment:** reject supplied MySQL query options explicitly rather than
  silently enabling a connection workflow broken in the frozen implementation.
  PostgreSQL TLS options remain supported and receive real-engine checks.
  Correct MySQL option merging is deferred as a future compatibility change.
- **Status:** retained oracle exception and native failure in P06 TLS evidence.

### RW-031: SQL/AWS dependencies expose newer host glibc symbols

- **Kind:** reproduced and repaired packaging regression, P06.
- **Evidence:** the expanded host binary failed in the existing Debian Bookworm
  container with GLIBC_2.38/2.39 requirements. `readelf` identifies `fmod`,
  `__isoc23_strtol`, and weak `pidfd_spawnp`/`pidfd_getpid` imports. Merely vendoring
  OpenSSL does not lower the libc ABI of application/dependency code.
- **Treatment:** extend the existing pinned AL2023 native build (including Perl
  for vendored OpenSSL), and allow the general bundle to consume those artifacts
  and native libraries. Preserve the deployment baseline rather than raising it.
  Retain Cargo dependency notices in all bundles.
- **Status:** Repaired and requalified on final P06 artifacts: maximum glibc 2.34,
  exact decoded real-raster output in [Debian](validation/evidence/p06/container.json)
  and [official AL2023](validation/evidence/p06/lambda-runtime.json).

### RW-032: Legacy profiler import is unavailable in the frozen environment

- **Kind:** inspected legacy dependency incompatibility, P07.
- Frozen Flask `create_app(profile=True)` imports `werkzeug.contrib.profiler`,
  removed by the pinned Werkzeug. The compatibility adapter uses the maintained
  `werkzeug.middleware.profiler.ProfilerMiddleware` with the same restrictions.
  It profiles the WSGI boundary; native raster internals require native profiling.
  This is an explicit replacement of a broken integration, not an ignored flag.

### RW-033: Legacy Python connection contexts commit individual operations

- **Kind:** inspected legacy behavior, P07; continuation of RW-021.
- Each nested `connect()` in the frozen relational store commits on successful
  exit. The adapter preserves already committed inserts after an outer failure.
  Native `DataStore.batch` remains atomic. Do not promise that Python's outer
  context rolls back all its completed method calls.

### RW-034: Python futures use spawn to avoid forking live native state

- **Kind:** internal execution decision, P07.
- The replacement retains `concurrent.futures.Future`, compressed Python raster
  cache, process-pool fallback and synchronous/asynchronous semantics. Its three
  Python workers use `spawn` and explicitly receive settings; each raw-raster
  session uses one GDAL worker. This avoids inheriting live native mutexes and
  queues through fork. `USE_MULTIPROCESSING=False` uses one calling thread.
  Executable ingestion scripts must use Python's usual `if __name__ ==
  "__main__"` guard for process-based raster retrieval, or disable multiprocessing.

### RW-035: P07 found public array precision and preview-bounds gaps

- **Kind:** reproduced and repaired port regressions.
- Python `compute_metadata` returns binary64 NumPy percentile arrays; stored
  metadata returns float32-decoded Python lists. Preserve precision until writing
  the existing float32 blob. The earlier preparation-only JSON adapter exposed
  storage precision too early for this newly implemented public return contract.
- Direct `get_raster_tile(tile_bounds=None)` uses transformed source bounds, not
  the suggested warped raster's rounded extent. A retained real Sentinel-2
  nonsquare array comparison exposed shifted samples and two changed mask pixels.
  Use densified source bounds for this path; HTTP's explicit bounds are unchanged.
- Evidence: P07 real-corpus integration compares dtype, every sample and mask,
  full percentile values, then stored float32 lists. This is fixed, not a tolerance.

### RW-036: Legacy CLI ANSI tests depend on Click's capture behavior

- **Kind:** reproduced frozen-environment test incompatibility.
- Two unchanged `test_logging_visible` cases require ANSI escapes, but the pinned
  Click runner strips them. Both frozen oracle and replacement produce ` [+] test`
  and fail the same assertions. Log visibility/levels and content are preserved;
  no production behavior is changed to satisfy a capture-format assertion.
- Evidence: `validation/evidence/p07/oracle-cli.xml` and `upstream-boundary.xml`.

### RW-037: boto monkeypatch tests do not intercept a native S3 client

- **Kind:** validation-boundary difference, with two repaired adapter bugs.
- The full unchanged Python suite produced 526 passes and eight failures. Six
  were remote-driver tests: four depend on boto's in-process moto interception
  and/or private mutable cache fields; those cannot exercise the Rust network
  client. Two also exposed real boundary bugs: invalid URL and missing-object
  errors must occur during driver construction. Both are repaired.
- Retain the failed run. The final unchanged applicable suite excludes this
  six-test file explicitly (526 passes, the two RW-036 failures, no skips).
  `test_invalid_url` separately passes unchanged. Thirteen public remote-driver
  assertions use moto's **socket server**: eager download/errors, read-only
  methods, metadata types, SQLAlchemy connection, TTL reconnect, pinned context
  generation, retirement and owner cleanup. This is emulator evidence, not AWS.
- Public contexts continue to yield actual SQLAlchemy connections. Ordinary
  methods run in Rust, check a borrowed SQL connection for failure, and preserve
  nested-context identity. Native remote refresh occurs between Python contexts;
  a context keeps one generation even if TTL expires before exit.
- Evidence: [P07](validation/evidence/p07/README.md), `contexts-fixed.xml`,
  `remote.json`, and `upstream-final.xml`. No test assertion was weakened and no
  in-process mock failure is counted as a pass.

### RW-038: Native settings parser needed boundary coercion and path validation

- **Kind:** repaired P07 integration regressions.
- Match the legacy integer coercion for explicit numeric/list settings and its
  writable-parent check for `REMOTE_DB_CACHE_DIR`. Defaults, each environment
  setting, explicit/config/CLI precedence, deprecated aliases and these edge
  cases now have 46 direct native-versus-oracle comparisons. Settings are scoped
  per native session and passed explicitly into spawned workers.
- Evidence: `validation/evidence/p07/settings.json`; no accepted deviation.

### RW-039: Detached raster workers can race native process shutdown

- **Kind:** reproduced P08 release defect; repaired and locally qualified.
- The unchanged P07 Lambda zip exited 139 on the initial official AL2023 test
  and two of 30 isolated repeats (`validation/evidence/p08/lambda-repeat.json`).
  The same command also succeeds; one successful smoke cannot clear this defect.
  Forty host debugger runs did not reproduce the crash, so no native backtrace
  is claimed. Inspection found that RasterExecutor discarded every JoinHandle:
  dropping its sender initiated cleanup but allowed process finalization while
  GDAL worker thread-local destructors still ran. The precise crash stack is
  unobserved; shutdown racing is the evidence-based working diagnosis.
- Own workers with their queue, close admission, drain accepted work and join
  every thread before releasing the executor. Apply the same ownership guard to
  startup failures. A regression requires queued work and actual thread-local
  destructors to finish before owner drop returns. Request timeouts still do not
  cancel GDAL; destroying an application now explicitly waits for native drain.
- Final artifact repetitions, adapter/lifecycle/failure tests and full combined
  checks are retained under P08. No failed old-artifact run is counted as passed.

### RW-040: Qualification inventory and harness corrections

- **Kind:** P08 evidence reconciliation; no application compatibility change.
- Ninety-four detailed records still said not-started although P05/P06 had
  implemented their families; P07 updated only its assigned detailed records.
  Reconcile per record with source/test/evidence pointers, retaining real AWS,
  nonlocal platform and external basemap gaps. Internal constants/type variables
  are inspection records, not independently executed public features.
- Initial P08 geography checks passed but contact-sheet assembly rejected palette
  PNGs; convert to RGBA explicitly. Stop process sampling before termination to
  avoid transient /proc access errors while a child exits. Isolate the BigTIFF
  range server in a separate process: the Python GDAL open can hold the GIL and
  stall an in-process HTTP server. Preserve failed logs; these are harness fixes,
  not weaker comparison rules or changes to the frozen oracle.

- P08 final browser review found one default-queue overload despite an intact RGB
  view. The browser harness now rejects any backend error; the qualified run
  uses a documented queue of 64. Original responses/screenshots are retained in
  `validation/evidence/p08/browser-final` and reviewed separately. This changes
  test/operator configuration, not native defaults or legacy client assets.

### RW-041: HTTP previews must use transformed source bounds

- **Kind:** reproduced P08 geographic port regression; repaired HTTP framing.
- A contiguous native-pixel mosaic of the real Copernicus E179/W180 DEM tiles
  spans 179–181 degrees after longitude unwrapping. Python derives preview bounds
  using Rasterio's source-to-target transform, including longitude wrapping.
  The native HTTP adapters instead projected the metadata's WGS84 bounding box,
  producing a materially different image. P07 already fixed direct Python raster
  framing (RW-035), but HTTP still supplied the old explicit metadata extent.
- Pass unspecified bounds for all HTTP previews (singleband, RGB and compute),
  allowing each raster reader to derive its own transformed source bounds just
  like Python. XYZ tile bounds remain explicit. Retain exact polar/dateline and
  all existing HTTP regressions, including independent RGB-source framing.
- Retained VRT XML exposed a second cause after fixing bounds: GDALWarp inserts
  `CENTER_LONG=180`, whereas Rasterio's WarpedVRT does not. This created two
  extra opaque edge columns. Set transformer option `INSERT_CENTER_LONG=FALSE`
  to preserve Rasterio's wrapping and masks; pinned GDAL 3.9.3 source confirms
  the option (`validation/evidence/p08/gdal-transformer-source.txt`). All 30
  geographic cases, including singleband/RGB/compute dateline previews, now
  compare exactly. Diagnostic probes were removed from production source.
- The oracle's whole dateline mosaic preview uses a near-global Mercator extent
  and is transparent at this thumbnail size. Preserve this legacy behavior;
  inventing a visually preferable dateline preview is outside the initial port.
  Tile, mask and category behavior remain separately checked.
- Evidence: P08 `before-dateline/geography-final.log` and retained image pairs show the material pre-fix failure;
  final qualified geographic reports and image pairs use the repaired candidate.

### RW-042: Large float32 reductions exceed the small-crop moment bound

- **Kind:** accepted, explained numerical difference; no runtime change.
- The real dateline mosaic has 17,280,000 valid float32 samples. Frozen NumPy
  reductions give mean 4.362151622772217 m and standard deviation
  38.27044677734375 m. Independent float64 reductions give 4.362156338360042 m
  and 38.27065278496088 m; the native values exactly equal these rounded to
  float32 (4.362156391143799 and 38.270652770996094).
- The original four-ULP small-crop bound fails here. Retain the failed report and
  log, prove both reduction paths on this input, and accept only this pinned
  full-mosaic case within 0.000005 m mean and 0.00021 m standard deviation.
  Other geographic cases retain RW-024's four-ULP or strict chunked bounds.
  This is sub-millimetre elevation roundoff, not changed statistical meaning.
- Stored percentiles, ranges, validity percentage, bounds and hull are exact.
  Rendering uses ranges/percentiles rather than these moments, and all 30
  geographic rendered images remain exact. No global numeric/pixel tolerance
  was widened. Evidence: `validation/evidence/p08/geographic-metadata.json`,
  `mosaic-reduction.json`, and `geographic-metadata-initial-failure.log`.

### RW-043: P08 compared unequal aggregate GDAL block-cache budgets

- **Kind:** benchmark configuration defect found during post-qualification
  bottleneck investigation; earlier equal-budget performance wording corrected.
- `GDAL_CACHEMAX=32` gave one threaded native process 32 MiB total but three
  Gunicorn processes 96 MiB total. Equal CPU/app-cache settings do not fix this.
- A bounded native diagnostic with exact images reduced concurrent RGB preview
  p50 from 1434 to 768 ms and DEM from 113 to 60 ms by changing only native GDAL
  cache to 96 MiB. Preview CPU work fell 25.83→13.58 seconds for 18 requests.
  Splitting into three native processes at the same total cache gave similar
  results (763/61 ms); shared-cache pressure is the dominant identified cause,
  rather than Rust instruction speed or demonstrated mutex contention.
- Keep the original matrix as observations of its documented configuration,
  not equal aggregate-cache proof. Rerun the repeated full matrix at matched
  aggregate GDAL budgets before final performance qualification. No production
  or sealed archive changes were made. See
  `validation/evidence/p08/bottleneck-followup/README.md` and its raw results.
- Cached compute retains a separate suspected per-pixel interpreter overhead
  (`src/expressions.rs`); it has not received isolated stage profiling yet.

### RW-044: Map browsing requires viewport-completion measurements

- **Kind:** user-directed performance coverage correction; no runtime change.
- Repeating one cached tile does not represent navigating through new imagery.
  Measure all newly visible tiles for 1024×768 views, six requests in flight per
  session, 1/4/8 spatially offset sessions, initial views and six successive pans.
  Each session retains downloaded tiles and pauses 100 ms after completion.
- Use full original Sentinel-2 COGs and native overviews. Initial bounding-box
  tracks contained extensive nodata and were rejected; zoom-13 valid-footprint
  tracks are 100% opaque. A first corrected attempt briefly overlapped the old
  pilot, was stopped and excluded. Final measurement ran alone.
- Match three CPU cores, 96 MiB aggregate GDAL cache and 64 MiB logical raster
  cache. Three trials per engine/mode/user count: 36 runs, 4,992 successful
  requests, 412 unique URLs, all decoded images exact.
- Typical pan completion improves 1.15× singleband / 1.28× RGB for one session,
  1.67× / 1.42× for four, and 1.92× / 1.50× for eight. At eight sessions RGB
  p95 improves 1,585→935 ms. These are local server-delivery timings, not browser
  frame rate, WAN latency or continuous-drag/cancellation results. Queue capacity
  256 is documented; no retries conceal overloads.
- Evidence and reproduction: `validation/evidence/p08/navigation/README.md`.
  Optimize this workload and its p95 under load for release-facing claims.
  Expressions and repeated-image microbenchmarks remain secondary diagnostics.

### RW-045: Exact affine Web Mercator path exposes the map-navigation bottleneck

- **Kind:** optimization opportunity and scoped compatibility contract; no accepted image difference.
- The original UTM COG navigation benchmark spends most time opening/setting up GDAL warp reads and decoding overlapping source windows. Worker-owned source reuse and suggested-geometry reuse help, but do not yield a general 10× improvement. Reprojecting the same real RGB inputs to Web Mercator is a fair comparison only if Python receives the identical COGs; the 10× claim must be attributed to the Rust sampling/cache/concurrency path, not the reprojection itself.
- For the qualified UInt16, north-up, EPSG:3857, fully valid tile interior, bilinear-plus-average path, Rust performs the affine source window and bilinear sampling while GDAL retains COG I/O/decoding and final average resampling. In 412 distinct URLs, decoded RGBA output matches the frozen Python reference exactly. Other cases use the legacy-equivalent GDAL path. One-stage average and shared zoom-wide VRT trials produced image differences and were rejected; these are not accepted deviations.
- A bounded raw-raster neighbor cache is useful only when cache hits continue scheduling their next neighbors and idle GDAL workers are awakened after the HTTP response. Source-block ring prefetch running alongside it consumed CPU and increased one-user RGB latency. An unfiltered PNG enlarged output by about 27%, so default compact encoding was retained.
- The three-trial paired median pan speedups are 11.84× singleband / 11.56× RGB for one user, 15.86× / 12.17× for four, and 34.19× / 27.32× for eight. One single-user RGB trial was 9.62×; first views are below 10×. Original UTM fallback remains exact and one-user 2.79× / 2.14×. See [map10 evidence](validation/evidence/p08/map10/README.md) and D-012. Broader CRS, masks, data types and remote delivery remain separate performance work, not silently included in this claim.

### RW-046: Original UTM COG fast path trades GDAL's rotated-chunk smoothing for faster interior bilinear sampling

- **Kind:** accepted, explained visual difference for a narrow opt-in performance path; general GDAL output is unchanged.
- The unmodified EPSG:32632 Sentinel-2 COGs remained only 2.14–2.79× faster for one user with the original general path. A Rust four-sample bilinear warp over shared decoded COG blocks reduced warm per-band work and, with a 112 MiB shared block cache and idle tile-neighbor prefetch, passed all three 1/4/8-user singleband/RGB pan trials above 10×. The weakest paired ratio was 13.24× at eight-user RGB; three-trial median gains are 24.66×/30.94× (one), 23.94×/16.09× (four), 33.44×/13.77× (eight). First-view and loaded RGB p95 do not reach 10×.
- A manually bilinear-warped tile does not compare byte-for-byte with Rasterio/GDAL. GDAL's warp kernel chooses a resampling footprint from each rotated chunk's source-window scale; its intermediate is slightly smoother. An exact GDAL warp over an in-memory cropped source matched the Python tile byte-for-byte but missed the loaded RGB speed goal (8-user pan 362 ms versus Python 1282 ms in a one-trial check). The chosen Rust path is faster (final 8-user RGB median 91 ms versus Python 1258 ms) with a small explained visual difference. The strict raw benchmark correctly retains `passed=false` and 412 distinct decoded-image mismatches.
- Across all 412 real benchmark images, alpha masks match exactly; the greatest per-image RGB mean absolute difference is 0.609/255, the largest per-image p99 channel difference is 4/255, and the maximum difference is 68/255 at one pixel of the worst RGB tile. Only two pixels in that 65,536-pixel tile differ by more than 16/255. Inspected worst-case singleband/RGB ordinary-scale pairs and a 2×2 neighboring RGB mosaic show no visible texture or seam change. This acceptance is specific to zoom-13 near-native, fully valid UInt16 UTM interiors; it is not a global pixel tolerance.
- The opt-in checks reject other CRS/data types, alpha or nodata in the padded source crop, source edges, magnification and overview-scale reads. These retain the exact general GDAL path. Verified sampled zoom-12/14 and partial/transparent edge tiles against the Python reference exactly, including 3.3% and 28.6% coverage. The fast path currently recognizes EPSG:32632 only. No broader UTM, mask-edge, remote or browser-frame performance claim follows.
- [Full evidence, images and reproduction](validation/evidence/p08/map10-utm/README.md), [raw paired report](validation/evidence/p08/map10-utm/report.json), [corpus-specific gate](validation/tools/p08_utm_qualify.py), D-013.

### RW-047: The UTM fast path bypassed an existing per-worker GDAL geometry cache

- **Kind:** performance opportunity repaired; no new compatibility difference.
- The original UTM fast path called `suggested_geometry()` on every tile and band, while the general GDAL route already cached that result in the worker-owned source entry keyed by file size/mtime. Both paths now use the same cache helper. The suggested geometry is independent of tile bounds for this source, so reusing it preserves the raster computation; an alternating Rust A/B verified all 412 decoded images exactly unchanged.
- Three alternating same-core/same-cache A/B trials show eight-user RGB pan p50 79.57→52.23 ms (1.52×), and four-user RGB 30.20→22.70 ms (1.33×). All three paired eight-user RGB trials improved by 1.43–1.68×. Single-user RGB did not improve consistently (6.85→8.29 ms A/B medians), and no single-user gain is claimed. Frozen-Python requalification of the candidate yields eight-user RGB 1,269.95→55.01 ms (23.09×), with all 18 paired trial cells above 10× and the same RW-046 visual bounds.
- One-trial worker-count probes at the same three-core affinity found six workers slower (eight-user RGB pan p50 74 ms) and two workers slower still (108 ms) than the three-worker 55 ms median. Retain three. Do not assume more worker threads improve this CPU-bound path.
- Evidence: [direct A/B and full requalification](validation/evidence/p08/map10-utm-geometry/README.md). The previous UTM report is retained as the pre-cache baseline. Candidate SHA-256 `3fa0b076f977a62a97e6247aa64bb0a64aecbe6356971f26c59a7cc861536ba5`.

### RW-048: Fresh first-view latency is dominated by cold GDAL/PROJ work and viewport fanout

- **Kind:** performance investigation; no new compatibility difference or adopted speedup.
- The qualified original-UTM zoom-13 first viewport contains 12 tiles per session (36 separate source-band reads for RGB) with only six in-flight tile HTTP requests, while a pan adds three or four tiles and can reuse opened sources, decoded blocks, application cache and idle neighbor prefetch. The three-trial Python/Rust first-view speedups are 3.06×/4.42× (one user singleband/RGB), 3.94×/5.22× (four), and 5.33×/6.09× (eight), versus >10× for all paired pan p50 trials.
- Temporary per-band profiling on the real one-user RGB workload found the first three coordinate/transform stages at 33.7–37.8 ms and source-window/block stages at 24.9–27.4 ms; remaining profiled bands had median 0.69 ms coordinates, 0.80 ms source windows, 1.47 ms interpolation and 0.44 ms average. These overlapping stages explain cold-source penalties but are not an additive viewport breakdown. Local OS disk cache was warm; service-local caches and coordinate operations were cold.
- Three alternating Rust A/B trials with the same executable found that turning off tile-neighbor prefetch produced no robust first-view gain (one-user RGB median 158.85→151.90 ms, eight-user RGB 731.15→755.58 ms), while one-user RGB pan worsened 7.01→26.35 ms. All 206 decoded images matched. Keep prefetch. Potential projection/block reuse needs a measured implementation; prewarming alone moves work to startup. [Evidence and limits](validation/evidence/p08/map10-utm-first-view/README.md).

### RW-049: Block singleflight and reusable GDAL transformer do not reliably shorten the first UTM viewport

- **Kind:** measured, rejected performance opportunities; no compatibility change retained.
- A shared `OnceLock` per source-block key prevented duplicate concurrent GDAL decodes without holding the cache mutex during I/O. It matched all 412 prior Rust decoded images in three alternating full-matrix A/B trials, but eight-user RGB first-view median was 679.15→692.18 ms and only one of three pairs improved. Instrumented whole-run duplicate waits were just one for one-user RGB and 15 for eight-user RGB; the three RGB bands use distinct TIFFs. Removed the synchronization state.
- GDAL's `GDALSetGenImgProjTransformerDstGeoTransform` allowed a worker-local source-stamped GenImgProj transformer to be reused with a fresh tile grid and approximation wrapper. The final candidate matched all 412 prior Rust images in a three-trial full-matrix A/B. In a further nine-trial RGB A/B, first-view median worsened 153.57→156.07 ms at one user (2/9 pairs improved) and 711.95→725.46 ms at eight (5/9 pairs improved). Pan medians improved modestly, but the intended cold-view gain did not hold, so transformer caching and its handle lifetime complexity were removed.
- A prior candidate build passed the frozen-Python narrow UTM qualifier and six exact fallback cases. After removing both experiments, the rebuilt binary SHA-256 returned to the qualified `3fa0b076f977a62a97e6247aa64bb0a64aecbe6356971f26c59a7cc861536ba5`. [Detailed A/B evidence](validation/evidence/p08/map10-utm-first-view-followup/README.md). Distinct cold COG decodes and first-time projection initialization remain; prewarming must be evaluated with startup time included.

### RW-050: Source wheel build needed a root package entry and CLI rename

- **Kind:** packaging gap repaired; user-requested CLI compatibility difference.
- The prior `build-wheel.sh` required a manually built native bundle and a reference Python environment. The repository root had no PEP 517 configuration, so `pip install .` could not compile/package the Rust core and Python CLI in one command.
- A source setuptools build now invokes the existing locked Rust release build and Linux native bundle path, compiles the CPython extension for the active interpreter, and retains the browser/API assets. The installed console command is `rcfp` as explicitly requested. The Python import package remains `terracotta` for compatibility; the native bundle executable retains its internal `terracotta` filename.
- The local CPython 3.12/Linux build, wheel install, direct source install, real-catalog native read, source archive and legacy staged-wheel checks pass. The staged helper previously failed on relative `PYTHON` after changing directories; it now preserves the virtual-environment symlink while making the path absolute. Offline cached build mode was used; online dependency resolution and other platforms remain unverified. [Evidence](validation/evidence/p08/source-pip-rcfp/README.md), D-015.

### RW-051: Rasterio 1.4.3 lacks a Linux arm64 PyPI wheel

- **Kind:** distribution coverage gap found while designing clean-wheel CI; no runtime behavior change.
- PyPI's Rasterio 1.4.3 file list includes CPython 3.12 wheels for Linux x86_64, macOS Intel/arm64 and Windows x86_64, but no Linux arm64 wheel. The current package pins rasterio==1.4.3, while the prepared platform CI uses conda on arm64. Consequently, a green conda-based arm64 smoke would not prove that an ordinary pip install of the intended PyPI wheel works there.
- Before publishing a Linux arm64 wheel, qualify a Rasterio release with an arm64 wheel against the frozen oracle, supply an independently built compatible Rasterio wheel, or explicitly omit that target. Keep exact built-wheel install tests and GDAL library-closure checks as release gates. [PyPI 1.4.3 files](https://pypi.org/project/rasterio/1.4.3/).

### RW-052: RCFP owns its Python distribution and import namespace

- **Kind:** user-directed, accepted Python packaging/API identity difference.
- The first source-built wheel still installed `terracotta` and `terracotta_rust` Python packages under a `terracotta-rust` distribution. The user clarified that this is a separate project: install `rcfp`, use `import rcfp`, and invoke `rcfp`. The current wheel therefore installs `rcfp` and its private native adapter `rcfp_native`, without a `terracotta` import shim or console entry. Public driver/settings/error class names and HTML/API document titles use RCFP.
- This changes Python import paths and CLI invocation for existing callers. The shared Rust core still reads the legacy database format and speaks protocol 0.10.1; `rcfp.__version__` is implementation 0.1.0 and `rcfp.__protocol_version__` is 0.10.1. Migration, connect, database version validation and Swagger negotiation use the protocol value. The separate native Rust bundle retains its historical executable/library filename; the wheel omits that executable.
- A first renamed wheel inadvertently included both old Python package directories from stale `build/lib`. The source `build_py` now clears the project package directories in that generated tree before copying current packages. The final CPython 3.12/Linux wheel, fresh install, direct source install, staged wheel, source archive, real raster tile, Flask/WSGI and CLI checks pass. Other platforms and clean online dependency resolution remain unverified. [Evidence](validation/evidence/p08/rcfp-identity/README.md), D-016. RW-050/D-015 describe the prior package identity and are superseded for Python distribution naming.

### RW-053: RCFP package must exclude the original Terracotta source

- **Kind:** user-corrected package boundary and project identity.
- Renaming the retained original `python/terracotta` source to `python/rcfp` made the reference look like the product. The original source was restored unchanged. The wheel now includes only the separate RCFP adapter, native extension, bundled executable and runtime. Cargo package/executable and shared library are named `rcfp`/`rcfp_core`. Legacy `TC_*` configuration, HTTP paths and database encoding remain protocol compatibility.
- The old `python/terracotta_rust` adapter was removed after explicit user approval; the original `python/terracotta` remains untouched. Automatic approval review initially rejected deletion before that approval. The current RCFP Python package supplies request, data-store and WSGI APIs. The original Terracotta driver/Flask package is not carried into RCFP, so old C27/C28 full-API claims do not apply to this wheel. [Current evidence](validation/evidence/p08/rcfp-source-boundary/README.md), D-017.

### RW-054: Rasterio cannot serve as an implicit native GDAL link target

- **Kind:** packaging dependency and platform validation gap. Rasterio wheels vendor GDAL/PROJ privately; depending on `rasterio` gives Python an installer-managed package but does not provide a stable shared-library path or a portable native ABI contract for the separate Rust executable.
- Pin Rasterio 1.4.4, bundle a private GDAL 3.10.3 runtime and data, repair transitive wheel dependencies, then install the final wheels in clean pip environments. The old 1.4.3 Linux arm64 dependency gap (RW-051) is resolved at package selection, subject to actual hosted arm64 installation.
- A local Linux CPython 3.12 wheel passed pinned Sentinel pixels and installed-package routes. A source metadata oversight initially let stale `egg-info` supply obsolete requirements; explicit `install_requires` and a rebuilt wheel now declare Rasterio 1.4.4 and `>=3.12,<3.13`. No macOS/Windows/arm64 or PyPI result is claimed before CI runs. [Packaging contract](packaging/CI.md), D-018.
