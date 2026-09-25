# Delivery phases

These are execution briefs, not elapsed-time promises. Each assigned phase must
be implemented, validated, and handed off end to end. Read
[WORKFLOW.md](WORKFLOW.md) and [VALIDATION.md](VALIDATION.md) before starting.
Update [STATUS.md](STATUS.md), [COVERAGE.md](COVERAGE.md), and relevant discovery
entries as actual work progresses.

Source paths below are relative to the separate Python reference repository.
Evidence must identify the frozen source/environment, not just a mutable path.
Phase exits are evidence gates, not requests for user approval. A missing external
check leaves that check pending without preventing useful independent work.

## Dependency map

Default order: P00 → P01 → P02 → P03 → P04 → P05 → P06 → P07 → P08.

P04 and P05 may proceed independently after P03 when shared interfaces are stable.
P06 depends on P05 write/metadata semantics. P07 needs P04–P06 plus the P03
adapter design; P08 needs all earlier exits. A blocked cloud check in P03 need
not halt independent P04/P05 implementation, but remains a release gap. Record
any other dependency exception and its integration risk before relying on it.

Do not start with a broad mechanical translation. Keep a runnable slice after
P01, and test each extension through the same entry points and reference runner.

## P00 — Acquire real rasters, freeze the reference, and build validation foundations

**Outcome:** another agent can reproduce the Python oracle, run comparisons,
and identify every unimplemented surface without guessing. This is the first
phase (IDs remain zero-based); acquiring the corpus is implementation work in
this phase, not a recommendation to leave for later.

**Context:** REWRITE.md; all of REWRITE_LOG.md; `setup.py`, `setup.cfg`,
`environment.yml`, `tests/conftest.py`, `tests/benchmarks.py`, server schemas,
driver APIs, CLI commands, and CI workflows in the reference;
[DATASETS.md](DATASETS.md) for corpus selection, acquisition, and stress tiers.

**Deliver:**

- Verify the full source commit; create an isolated reference snapshot and
  environment, record dependency/native-library versions, and preserve the
  original checkout. Pin an environment that can run the reference; document
  any baseline failures and remediation without quietly patching its behavior.
- Expand COVERAGE.md to an auditable feature/contract inventory with source and
  test pointers. Audit settings, flags, expression functions, and public methods.
- Create the initial Rust build skeleton with reproducible dependency/toolchain
  choices and clear module boundaries. Avoid speculative subsystems.
- Implement a reference capture and comparison runner with a documented command
  entry point. Initially compare captures to themselves and controlled altered
  captures; a Rust renderer is not needed to prove the comparator works.
- Download/cache a small real-world raster corpus from community sources using
  DATASETS.md. Pin exact assets and provenance; extract native-resolution subsets
  with recognizable features for routine tests. Cover imagery, continuous data,
  categories, projection and mask differences with roughly 3–4 source scenes.
  Preserve representative original encodings rather than converting everything
  into one idealized COG profile. Justify any synthetic edge-case microfixture.
- Acquire a full-size or sufficiently large real-data case for the stress tier.
  Record file bytes, decoded size, TIFF variant and offsets, overview structure,
  and measured Python memory/I/O behavior. Small crops alone cannot clear this
  requirement; mark actual large-file/BigTIFF gaps explicitly.
- Build reference databases from those inputs and capture metadata, HTTP/images,
  and error cases. Produce a contact sheet or browseable visual index with known
  locations, tile coordinates, settings, and provenance for future comparisons.
- Establish evidence manifests, artifact locations/retention, ignored generated
  paths, and basic CI commands. Record a small baseline performance sample as
  context, not as a production performance claim.

**Exit evidence:** a clean environment can build the skeleton and recreate the
reference captures; comparator checks detect altered status, keys, masks,
dimensions, visible pixels, and storage values. Any baseline/environment failure
has a reproducible record. All initial contracts are inventoried, even if their
fixtures are scheduled for later phases. Pinned real assets and subset recipes
can be reacquired, ordinary checks run from the local cache without live downloads,
and the large-raster reference smoke has evidence. A README documents actual
download/subset/capture commands and expected storage/transfer sizes. An unfetched
source shortlist does not satisfy corpus acquisition.

**Boundary:** no full server, cloud rollout, or exhaustive corpus is required.
Do not spend this phase perfecting numerical tolerances without a Rust output.

## P01 — Serve an existing SQLite dataset end to end

**Prerequisite:** P00 reference/capture tooling and build foundation.

**Outcome:** the new executable reads an existing database and serves a useful
singleband tile and preview with compatible appearance and metadata.

**Context:** `drivers/sqlite_meta_store.py`, `drivers/relational_meta_store.py`,
`drivers/terracotta_driver.py`, `raster.py`, `xyz.py`, `image.py`, singleband,
keys/datasets/metadata handlers and server schemas; RW-001–006, RW-008.

**Deliver:** SQLite read path and legacy version handling; ordered keys; basic
dataset and metadata discovery; the original two-stage warp/mask path; grayscale
and one supplied palette; singleband tile/preview HTTP routes; settings needed
for the slice; compatible empty/error responses; a minimal runnable serve command.
Keep GDAL ownership isolated and the rendering core usable without HTTP.

**Exit evidence:** differential API and decoded-image checks from an unchanged
Python-created database; representative projections, nodata/alpha and seams;
non-square and out-of-bounds requests; metadata precision/coordinate ordering;
side-by-side real-corpus imagery at normal display scales; bounded-memory tile
and preview reads against the retained large raster, with source/overview/transfer
details so a downsampled copy cannot masquerade as a large-file test;
documented local build/run commands. Preserve ordinary appearance without chasing
proved immaterial rounding. Unsupported options remain explicit coverage gaps.

**Boundary:** do not advertise complete HTTP or CLI compatibility yet.

## P02 — RGB, remote raster reads, and bounded execution

**Prerequisite:** P01 runnable slice.

**Outcome:** multi-band and remote workloads function under concurrent requests
with controlled resource use.

**Context:** RGB handler/schema, `geotiff_raster_store.py`, `cache.py`, GDAL
environment configuration, remote paths and raster tests, benchmark cases.

**Deliver:** RGB tiles/previews and stretches; GDAL remote raster access;
bounded scheduling, dataset ownership, queues, and cache identity; repeated-band
handling; meaningful timeout/overload behavior; instrumentation for memory and
remote traffic. Keep visible compatibility for requests admitted for processing.
Document any new limits and overload responses as operational decisions.

**Exit evidence:** differential partial-mask RGB cases, local/range-served remote
equivalence, warm/cold concurrency runs, cache correctness across settings,
bounded memory/handles, and failure recovery. Run the large-raster tier through
local and range-served reads, including detailed windows that touch base-resolution
blocks rather than only overviews. Exercise real S3 credentials and
custom endpoints if available; local tests do not clear those external checks.

**Boundary:** remote raster support does not yet mean S3 SQLite metadata support.
Do not claim a request timeout cancels native work without demonstrating it.

## P03 — Prove distribution and Python integration early

**Prerequisite:** P02 executable and shared core; local work may proceed while
P02's externally blocked cloud checks remain tracked.

**Outcome:** major packaging and Python-binding risks are resolved while changes
to the architecture are still cheap.

**Context:** reference Docker files, `server/app.py`, `server/flask_api.py`,
`docs/tutorials/wsgi.rst`, `docs/tutorials/aws.rst`, `zappa_settings.toml.in`,
Python driver public API; current platform requirements from P00 inventory.

**Deliver:** a container exercising real raster reads; a Lambda handler/build
artifact with native dependencies and binary response handling; a minimal Python
extension exposing a genuine driver/render operation; proof of the intended
Flask/WSGI adapter boundary; explicit implementation/protocol/database version
identity. Retain notices required when reusing code or assets.

**Exit evidence:** container smoke and differential image results; Lambda event
adapter tests and package loading; actual Lambda invocation where authorized;
Python import and return/error semantics; a WSGI-path smoke; a representative
database write/read round trip in both languages, even before the complete write
API is implemented. A clean platform matrix separates tested from pending.

**Boundary:** this is a feasibility slice, not complete Python support. Cloud
spending or deployment requires session authorization; finish local artifacts
first and state exactly which real-runtime check remains pending.

## P04 — Complete rendering and HTTP behavior

**Prerequisite:** P03 architecture evidence, with external exceptions recorded.

**Outcome:** all supported rendering modes and public HTTP contracts have
implementations and meaningful parity evidence.

**Context:** all `server/` and `handlers/` modules, `expressions.py`, `image.py`,
`cmaps/`, HTTP/CORS/field/expression/image tests, reference API specification.

**Deliver:** categorical and all built-in/custom palettes; all stretch and
color-operation semantics; compute parser/evaluator and masked arrays; remaining
RGB/singleband options; all previews; dataset filters/sort/pagination; batch and
selected metadata; colormap responses; validation/errors/CORS; API docs/spec.
Retain legacy version negotiation with old clients.

**Exit evidence:** contract matrix for every route and option; expression
precedence/negative arithmetic/invalid values/masks; categorical meaning;
palette and transform comparisons; meaningful error and URL encoding cases;
old client handshake and rendering checks. Record accepted differences narrowly.

**Boundary:** fix port errors now; log legacy quirks for later, including
inconsistent percentile naming. Do not substitute an unrelated expression DSL.

## P05 — Metadata, ingestion, and raster optimization

**Prerequisite:** P03 shared core/packaging direction; coordinate interfaces with
P04 if executing independently.

**Outcome:** users can prepare data and populate databases using existing flows.

**Context:** metadata/statistics functions in `raster.py`, driver write/lazy-load
paths, `cog.py`, `scripts/ingest.py`, `scripts/optimize_rasters.py`, raster-pattern
parsing, statistics and CLI tests, documented ingestion examples.

**Deliver:** whole-image, sampled/lazy, and chunked metadata; hulls/bounds and
extra metadata; transactional SQLite insert/delete/overwrite behavior; lazy-load
coordination; raster-pattern and RGB-key semantics; ingestion flags; optimization
with masks, overviews, compression/reprojection and memory/disk paths; existing
overwrite/skip/quiet behavior. Complete `serve -r` with lazy metadata.

**Exit evidence:** statistics/geometry comparisons with mode-specific limits,
all-invalid and constant rasters, Python/Rust database round trips, concurrent
lazy loads, real CLI ingest/serve workflow. Optimized files must be readable,
valid for the intended COG workflow, and yield equivalent visible tiles; their
compressed TIFF bytes need not match.

**Boundary:** changing quantile definitions, schema, or raster organization is
not authorized as an incidental cleanup.

## P06 — Complete metadata backends and migrations

**Prerequisite:** P05 metadata/write contracts and P02 remote I/O foundations.

**Outcome:** the supported storage choices and historical upgrade paths work
without forcing users to re-ingest data.

**Context:** every metadata-store driver, `migrations/`, `scripts/migrate.py`,
path/provider detection, driver integration tests, historical database fixture.

**Deliver:** MySQL and PostgreSQL read/write behavior; read-only S3 SQLite with
safe TTL refresh and connection retirement; provider detection, credentials/path
normalization, sorting/collations, transaction and failure semantics; supported
migration chains and CLI behavior. Preserve format compatibility identifiers.

**Exit evidence:** real local database-engine integrations, cross-language reads
and writes, overwrite/delete/rollback failures, isolated historical migrations,
read-only/lazy-load failure semantics, concurrent/failed remote refresh. Real S3
checks are distinct from emulator tests and remain pending if unavailable.

**Boundary:** no production database mutation. Test migrations on disposable
copies; preserve old data needed for rollback and never advance a version marker
without the associated successful operation.

## P07 — Complete Python, client, CLI, and operational integration

**Prerequisite:** P04–P06 and P03 adapter/package design.

**Outcome:** users can replace complete workflows, not just individual endpoints.

**Context:** public Python API/docs/examples, all CLI/settings tests, browser
assets and client glue, configuration/profile/log/cache behavior, deployment docs.

**Deliver:** remaining public Python methods/return types/context managers/
exceptions/futures; Flask/WSGI integration; all CLI flags and exit behavior;
configuration precedence and deprecated settings; `connect` browser workflow;
logging/profiling/tracing coverage; container/Lambda/platform distribution and
operator instructions. Reuse the browser client and wire its backend configuration.

**Exit evidence:** existing ingestion scripts through the new package, actual
Python and WSGI integration tests, settings precedence, CLI workflows, browser
exploration of tiles/previews/metadata, supported platform install/smoke matrix,
and operational checks linked to coverage. Distinguish a documented deployment
change from an unresolved integration gap; documentation alone cannot clear it.

**Boundary:** no frontend redesign. If a legacy profiler requires an alternative,
record the behavior and compatibility impact rather than silently ignoring flags.

**P07 execution scope adjustment (2026-09-24):** the user requested GitHub Actions
setup and deferred nonlocal platform testing until later. P07 delivers the
workflow, local install/runtime evidence and an explicit pending platform matrix;
unrun platforms are not qualified. See D-010 in [DECISIONS.md](DECISIONS.md).

## P08 — Qualify the complete replacement

**Prerequisite:** all earlier phase exits; unresolved external checks remain
explicit blockers to the corresponding release claims.

**Outcome:** a reviewable release candidate with honest compatibility and
performance claims and a tested return path to Python.

**Deliver:** complete coverage reconciliation, clean-environment installation,
full differential/integration runs, representative browser inspection, concurrent
soak/failure tests, fair Python/Rust benchmarks, migration/rollback instructions,
supported environments and limitations, and reproducible release artifacts.

**Exit evidence:** every inventory row is verified or has an explicit accepted
scope/compatibility decision; no material unexplained mismatch; no skipped check
presented as a pass; ordinary application appearance preserved; data read back by
Python after supported Rust writes; clean installation on declared platforms;
measured resource/performance results with conditions; findings and follow-up
issues fully linked. Unimplemented required features block full-parity claims.

**Boundary:** prepare artifacts and release notes. Publishing, tagging, merging,
or deploying follows session authorization; it is not implicit in this phase.
