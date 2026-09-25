# Architecture decisions

The defaults in [REWRITE.md](../REWRITE.md) remain authoritative project guidance.
Use this log for consequential implementation choices and evidence that changes
the defaults. Routine local decisions need no entry.

### D-001: Minimal dependency-free Rust foundation

- Date / phase / owner: 2026-09-22 / P00 / Codex.
- Status: adopted.
- Problem and constraints: P00 needs a reproducible Rust build and stable core
  boundary before a rendering implementation exists.
- Options actually considered: introduce the anticipated HTTP/database/GDAL
  stack now; create a locked dependency-free library and thin CLI identity.
- Choice and evidence: Rust 1.85.1, edition 2024, one core library and a CLI
  adapter skeleton. Offline build and tests pass; no unsupported server surface
  is advertised.
- Compatibility consequences and relevant RW IDs: none yet. Oracle protocol
  version and rewrite implementation version are separate constants for RW-003.
- Validation performed / outstanding: `cargo build/test --locked --offline` and
  `terracotta --version` pass. P01 must select and prove actual GDAL, SQLite, and
  HTTP dependencies through the vertical slice.
- Conditions that would justify revisiting: a supported target cannot use Rust
  1.85.1 or the P01 integration shows the core/adapter boundary is unsuitable.
- Supersedes / superseded by: none.

### D-002: Venice and Dolomites real-raster corpus

- Date / phase / owner: 2026-09-22 / P00 / Codex.
- Status: adopted.
- Problem and constraints: cover optical, compute, continuous, categorical,
  projection, mask, native COG, and decoded-memory behavior with a small corpus.
- Options actually considered: the DATASETS.md shortlist and a fourth projected
  source; three complementary products already cover the P00 dimensions except
  physical BigTIFF offsets.
- Choice and evidence: one pinned Sentinel-2 L2A Venice scene (RGB/NIR), one
  Copernicus DEM Dolomites tile, and one ESA WorldCover Venice tile. Seven
  native-resolution subsets and the full WorldCover source were acquired and
  verified. See `validation/reference/assets.json` and P00 evidence.
- Compatibility consequences and relevant RW IDs: the derived fast subsets are
  ordinary stripped GeoTIFFs and cannot prove parent COG organization. Full
  parents remain available for storage/access checks.
- Validation performed / outstanding: exact parent-window pixel/mask/geometry
  validation and visual inspection passed. A physically multi-GiB BigTIFF with
  offsets beyond 4 GiB remains required in P02.
- Conditions that would justify revisiting: an asset becomes unavailable or a
  later phase needs an uncovered native alpha/high-latitude/storage case.
- Supersedes / superseded by: none.

### D-003: Dependency-light P01 native adapters over the GDAL C API

- Date / phase / owner: 2026-09-22 / P01 / Codex.
- Status: adopted.
- Problem and constraints: P01 needed a runnable SQLite/HTTP/GDAL slice on a
  host without global GDAL development headers or a `gdal-config`, while keeping
  GDAL ownership isolated and the core independent of HTTP.
- Options actually considered: wait for/install a system GDAL development
  stack; invoke Python as the raster engine; bind the stable GDAL C API at
  runtime and use small Rust crates for the other boundaries.
- Choice and evidence: `raster.rs` loads the pinned GDAL 3.9.3 shared runtime
  and owns datasets/VRTs within a synchronous request; `storage.rs` uses bundled
  SQLite read-only; `image.rs` owns stretching/palettes/PNG; `http.rs` is a thin
  native adapter. The two-stage padded warp and separate resampling read exactly
  reproduce all retained P01 decoded images. P01 fast/stress evidence passed.
- Compatibility consequences and relevant RW IDs: legacy SQLite major/minor,
  coordinate-column and native float32 encodings are preserved (RW-001,
  RW-003, RW-006). Rasterio's private library directory is discovered only as a
  development fallback; it is not the P03 distribution design.
- Validation performed / outstanding: exact 26-case HTTP/image differential;
  full 36,000 × 36,000 COG at 41,926,656-byte sampled peak RSS. P02 must replace
  the single-request/fixed `/vsimem` name assumption with bounded concurrent
  worker ownership and qualify remote reads. P03 must package GDAL/data files.
- Conditions that would justify revisiting: packaged targets cannot use dynamic
  GDAL C loading, GDAL ABI qualification finds an incompatible supported
  version, or P02 worker isolation needs a different ownership layer.
- Supersedes / superseded by: extends D-001's P00 foundation; none.

### D-004: Bounded worker-owned raster execution and raw-raster cache

- Date / phase / owner: 2026-09-22 / P02 / Codex.
- Status: adopted.
- Problem and constraints: concurrent RGB and remote requests must not multiply
  GDAL handles, memory, or queued native work without bounds; response timeout
  cannot safely cancel an in-flight GDAL C call.
- Options actually considered: one global synchronous GDAL owner; unbounded
  request threads around per-call datasets; a bounded pool with worker-owned GDAL
  runtimes and a cache before image rendering.
- Choice and evidence: default to three raster workers, eight queued jobs, a
  30-second response wait, a 64 MiB raw-raster LRU cache, and a 60-second remote
  cache TTL. Local identity includes path, size/mtime, bounds, dimensions, and
  categorical mode; remote identity uses URL plus TTL. HTTP admission is also
  bounded. Limits are configurable through documented `TC_*` variables. P02
  exact differentials, saturation/recovery, and full-parent range stress pass.
- Compatibility consequences and relevant RW IDs: admitted RGB/singleband work
  preserves output. Full queues return 503 and elapsed response waits return 504.
  A timeout does not cancel native work; it remains counted until completion.
  RGB repeated bands are de-duplicated within a request. RW-005/RW-013/RW-014.
- Validation performed / outstanding: peak native concurrency never exceeded
  the configured worker count; 503/504/failure recovery passed; warm remote
  repeats added no traffic; fast peak RSS was 191,877,120 bytes and full-parent
  stress peak RSS was 54,407,168 bytes under 512 MiB. Real S3/custom endpoint,
  physical BigTIFF offsets, and client-disconnect behavior remain release gaps.
- Conditions that would justify revisiting: packaged GDAL has different
  initialization/thread constraints, production measurements require different
  defaults, or safe native cancellation becomes available.
- Supersedes / superseded by: extends D-003's request-owned P01 boundary; none.

### D-005: Relocatable GDAL bundle and one in-process adapter boundary

- Date / phase / owner: 2026-09-22 / P03 / Codex.
- Status: adopted.
- Problem and constraints: the P02 binary discovered GDAL in the reference
  virtualenv, while container, Python/WSGI, and Lambda paths risked duplicating
  rendering logic or loading an incompatible native runtime.
- Options actually considered: install system GDAL independently in each
  adapter; use subprocess HTTP calls from Python/Lambda; package the pinned
  native runtime and expose the existing `Application::request` path in-process.
- Choice and evidence: bundle the pinned Rasterio native libraries and
  GDAL/PROJ data relative to the executable, with retained notices. Native HTTP,
  a small C-ABI/CPython extension, WSGI, API Gateway events, and the custom
  Lambda Runtime API all call the same bounded application request path. Build
  Lambda in pinned `provided.al2023` and bundle its missing base libraries.
  P03 host, clean-container, fake-Runtime-API, and official-runtime-image checks
  all render exact decoded RGBA from the real corpus.
- Compatibility consequences and relevant RW IDs: explicit implementation,
  protocol, and database identities address RW-003; executable-relative loading
  resolves RW-012 for qualified Linux x86_64 targets; the target build resolves
  RW-015. Python exposes a deliberately minimal bytes/error surface, not P07
  API completeness. The P03 compatibility writer refuses replacement and
  copies one already-computed record; P05 owns full write semantics.
- Validation performed / outstanding: 21 local adapter/package checks, clean
  Debian container, pinned AL2023 image, Python↔Rust SQLite round trips, P01/P02
  regressions, Clippy/build/tests. Actual AWS invocation, arm64, macOS and
  Windows remain explicitly pending.
- Conditions that would justify revisiting: final wheels need stable-ABI or
  multi-Python support, a smaller audited GDAL build replaces the Rasterio
  runtime, Lambda changes its base ABI, or adapters require streaming.
- Supersedes / superseded by: extends D-003/D-004; none.

## Entry template

### D-NNN: Decision title

- Date / phase / owner:
- Status: proposed / adopted / superseded.
- Problem and constraints:
- Options actually considered:
- Choice and evidence:
- Compatibility consequences and relevant RW IDs:
- Validation performed / outstanding:
- Conditions that would justify revisiting:
- Supersedes / superseded by:

Keep historical entries when superseding a choice. If a decision changes the
high-level policy, update REWRITE.md and the affected coverage/phase plan as well.
Do not use this log to quietly waive an unmet public contract.

### D-006: Native bounded expression program and embedded reference assets

- Date / phase / owner: 2026-09-23 / P04 / Codex.
- Status: adopted.
- Choice: parse the frozen restricted Python grammar into a postfix program;
  evaluate with scalar/array identity, source dtype and explicit masks. This
  avoids embedding Python or adopting an incompatible generic DSL. Bound input
  bytes, tokens, nesting and elapsed evaluation; ordinary 1,000-term expressions
  remain supported. Raster work continues through D-004's bounded executor.
- Palettes, generated spec, rendered documentation and static assets are copied
  from the frozen reference, embedded at compile time, and audited by
  `validation/tools/p04_assets.py`. Custom uint8 `(255,4)` NPY maps load at startup,
  including Fortran order and built-in-name overrides; reread custom files for
  each request to preserve live replacement. Retain upstream notices.
- Shared request boundary now carries methods, bodies and headers, so native
  HTTP, WSGI and Lambda consume the same metadata/render/validation/CORS logic.
  The earlier four-item Python request result remains available; `request_full`
  carries headers/body without changing it.
- Evidence: expression/dtype vectors, complete palette image/legend matrix,
  real-raster settings and mask tests, [P04 evidence](../validation/evidence/p04/README.md).
  RW-016–020.
- Extend D-004/D-005 native lifetime handling: retain one process-lifetime GDAL
  library handle and register once under a global initialization lock. Worker
  datasets remain worker-owned. RW-020 demonstrates why worker-local unloading
  is unsafe before pthread TLS destruction; packaged concurrent lifecycle checks
  now pass.
- Remaining boundary: this does not complete P05 ingestion or P07 legacy Python
  APIs/settings/platform support; cloud/platform exceptions are unchanged.

### D-007: Shared native preparation, legacy statistics, serialized lazy publication

- Date / phase / owner: 2026-09-23 / P05 / Codex. Status: adopted.
- Keep metadata and raster preparation in the worker-owned GDAL C boundary.
  Full/sample paths use nearest source reads and linear percentiles; chunking
  traverses native blocks with Crick's compression-100 merging t-digest and
  online moments. Port that small algorithm with its BSD/Apache notices instead
  of embedding Python or substituting a different estimator. Use GDAL/GEOS for
  robust coverage hulls and GDAL for coordinate transforms.
- Keep legacy SQLite schema/version/blob encoding. Writes and deletes are atomic
  across dataset/metadata tables; caller-requested batches use rollback guards
  and savepoints. Legacy CLI ingestion commits individual inserts (RW-021).
- A lazy miss obtains `BEGIN IMMEDIATE`, rechecks metadata and dataset, computes
  at configured `TC_LAZY_LOADING_MAX_SHAPE` (default 1024×1024), and publishes
  atomically. This coordinates separate processes and holds one writer per DB;
  long computations serialize writes. Existing HTTP admission bounds requests.
  Read-only complete metadata stays readable; read-only lazy misses fail explicitly.
- Optimize via reference 256×256 reads, dataset masks, internal overviews and a
  final GTiff copy with the chosen compression. Support memory or sibling disk
  intermediates, cleaned by ownership guards; use bounded file workers for nproc.
- CLI, structured data operations and the P05 Python preparation adapter call
  this same core. Full legacy Python contexts/types/settings remain P07; the P05
  `DataStore.batch` is explicitly atomic and does not redefine legacy contexts.
- Evidence: [P05](../validation/evidence/p05/README.md), RW-021–025. Current GDAL
  bundle includes GEOS; future reduced native builds must retain it. Packaging
  keeps the process-lifetime GDAL handle from D-006/RW-020.

### D-008: Engine-owned queries and leased remote SQLite generations

- Date / phase / owner: 2026-09-23 / P06 / Codex. Status: adopted.
- `Store` is the shared HTTP/preparation/adapter facade over existing SQLite,
  synchronous native MySQL/PostgreSQL, and read-only remote SQLite. Preserve
  backend SQL schema, collation, quoted dynamic keys and bound values. Push
  sorting and pagination into SQL together; Rust sorting changes tied-row
  selection on MySQL (RW-026/RW-029). Keep format/protocol identity at 0.10.
- Relational writes use transactions and nested savepoints with unwind guards;
  lazy publication locks the dataset row and rechecks under that lock. Existing
  application admission and store mutex bound connections/work. Health checks
  reconnect retired connections before requests, never replay partially executed
  mutations. Native atomic batches retain D-007 semantics; complete legacy
  Python context behavior remains P07.
- Remote refresh streams a complete signed S3 GET into a unique private file,
  checks SQLite integrity/schema/version, then publishes a new generation under
  a refresh lock. Store leases keep old files alive until their connections
  close. Failed refreshes preserve the last valid file and leave TTL unchanged,
  while returning an error to the triggering caller. One batch uses one lease.
  No in-place overwrite, copied live connection, or stale-on-error response.
- Use the AWS SDK credential chain with rust-s3 signing/streaming; resolve it
  at refresh so named profiles, config files and rotating credentials work.
  Metadata credentials use standard AWS settings, separate from raster-specific
  `TC_RASTER_AWS_*`. Real AWS/SSO/role-service qualification stays external;
  local evidence covers environment/session and rotating named-profile credentials.
- Use the exact frozen migration statements for supported 0.7→0.10 upgrades.
  SQLite/PostgreSQL rollback whole chains. MySQL implicit DDL commits and its
  unsupported historical 0.7 SQL stay explicit, including restore-before-retry
  instructions (RW-027). Never publish a format marker before its operations.
- Preserve reflected MySQL Decimal HTTP conversion and legacy default-stretch
  failure (RW-028), plus malformed MySQL URL query behavior (RW-030). These are
  compatibility constraints, not new desired API designs.
- Package against the established pinned AL2023 baseline, with cached Rust
  dependencies and vendored TLS, then qualify on Debian and the official runtime
  image. The expanded host build imports glibc 2.38/2.39 symbols (RW-031); a newer
  deployment baseline is unnecessary. Retain dependency and vendored notices.
- Evidence: [P06](../validation/evidence/p06/README.md). Revisit if connection
  concurrency becomes a measured bottleneck, backend contracts change, or the
  supported platform matrix requires different TLS/native packaging.

### D-009: Owned Python sessions and retained compatibility glue

- Date / phase / owner: 2026-09-24 / P07 / Codex. Status: adopted.
- Ship both `terracotta` (legacy import/Click/Flask/NumPy contract) and
  `terracotta_rust` (explicit native adapter). Reuse frozen schemas, utility
  helpers and browser assets with attribution. Raster reads, statistics,
  preparation and ordinary metadata-store methods call the shared Rust core.
  Retain SQLAlchemy connection objects for public contexts and legacy Python
  migration scripts; these permit caller-supplied statements and historical
  schemas, and are not the native HTTP storage path.
- Python capsules own native store/application/executor sessions. Flask and WSGI
  keep an application alive across requests. Request settings use scoped thread
  snapshots inherited by GDAL workers, and thread-local GDAL options restored
  on drop; concurrent adapters do not rewrite process environment variables.
- Preserve NumPy arrays, masks, storage-decoded lists, Decimal reflection,
  ordered mappings and futures at the boundary. Computed percentiles remain
  binary64 until the existing database encoder casts to native float32.
- Python raster futures use spawn (RW-034), with settings transferred explicitly,
  and one native GDAL worker per raw-raster executor. Native HTTP remains Python
  independent. Python cache compression retains its legacy implementation;
  native cache limits count uncompressed native arrays and can use less than the
  same compressed Python budget. `TC_RASTER_CACHE_BYTES` overrides the legacy
  size setting for the native executor.
- Python profiling uses maintained Werkzeug middleware (RW-032); native
  `--profile` emits structured request timings and the existing resource metrics.
  Native X-Ray emits daemon subsegments using incoming trace context. Local
  daemon evidence cannot establish AWS delivery.
- Distribution: keep protocol/database version 0.10.1 separate from wheel/native
  implementation version 0.1.0. The wheel is platform/CPython-specific. The Linux
  standalone wheel includes GDAL; the CI platform wheel uses explicitly installed
  conda GDAL. No universal wheel or unsupported-platform claim.

### D-010: Defer nonlocal platform execution to prepared CI

- Date / phase / owner: 2026-09-24 / P07 / Codex. Status: user-authorized scope change.
- The user directed: “Set up CI via GitHub actions, we can test there later.
  Continue locally for now.” Deliver the runnable workflow and local validation;
  Linux/Lambda arm64, macOS Intel/arm64 and Windows x86_64 results remain pending
  until those jobs actually run. This changes P07's exit scope, not the release
  support claim. P08 must retain C32 as partial and inspect the uploaded reports.
- Use hosted runners and an installed GDAL environment for platform wheels, and
  build native Lambda artifacts inside AL2023 for each architecture. All runtime
  checks are local containers; no AWS deployment or publishing is configured.

### D-011: Join owned raster workers before application destruction completes

- Date / phase / owner: 2026-09-24 / P08 / Codex. Status: adopted.
- P08 repeated official-runtime invocations exposed intermittent exit-139 in
  the P07 artifact (RW-039). Retain worker JoinHandles with the admission queue.
  Drop closes the sender, drains already admitted jobs and joins all workers,
  including their native thread-local cleanup. Partial startup uses the same
  guard. Do not unload GDAL or abandon cleanup to process finalization.
- A persistent application's HTTP deadline/disconnect behavior is unchanged.
  Destroying an application or a stateless adapter waits for outstanding native
  work. This is explicit owner-lifetime behavior, not forced GDAL cancellation.
  Operators should reuse applications and bound native remote I/O timeouts.
- Evidence: P08 shutdown regression, repeated official AL2023 invocation,
  owner-release, overload/drain and combined adapter checks. Extend D-004/D-006/
  D-009 without changing raster algorithms, schema or transport responses.
