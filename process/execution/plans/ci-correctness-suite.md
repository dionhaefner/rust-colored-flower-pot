# CI correctness suite — implementation plan

Owner: RCFP maintainers. Prepared 2026-09-29. Scope: a substantial, repeatable
test suite and CI gates for the declared RCFP product. This document is a plan,
not evidence that the proposed jobs pass. It extends the current five-platform
build and installed-wheel smoke in [CI workflow notes](../../CI.md).

## Outcome and acceptance standard

Every critical path must have a **standalone RCFP test** with an independently
specified expected result, exercised through the public interface and real
storage/raster path where applicable. The Terracotta 0.10.1 release supplies a
separate differential suite during the transition. Deleting the oracle job later
must leave a useful, release-blocking correctness suite. A green wheel smoke or
historical local P08 report does not meet this standard.

Keep the [36 contract families](../COVERAGE.md) and
[386 detailed records](../../validation/reference/contracts.md) as the coverage
inventory. For each applicable family, record a named standalone test, an
assertion type, a CI lane, and the result. Any declared behavior without a test
is an open gap. The accepted `rcfp` namespace change (RW-052/D-016) and the
current partial C27/C28 surface need explicit dispositions, not tests that
pretend the old `terracotta` import or Flask interface ships in this wheel.

## Test design

Use three independent assertion sources, chosen per contract:

1. **Specified outputs:** small reviewed JSON/status/header and decoded-pixel
   expectations; known source pixel windows, coordinates, masks, categories and
   metadata values with provenance. Derive these from source data, reviewed
   calculations and documented protocol behavior, not from Terracotta captures.
   Include negative inputs and exact error class/status where contractual.
   Never regenerate these from the candidate.
2. **Invariants and round trips:** filters/pagination partition the known
   catalog; RGB channel permutations, compute identities and mask propagation
   behave as specified; adjacent tiles agree at seams; stored rows survive
   reconnect, overwrite/delete and rollback; optimization preserves source
   values, masks and bounds. These catch errors even when a frozen image was
   accidentally produced by the same buggy code.
3. **Frozen Terracotta comparison:** run the checksum-pinned PyPI 0.10.1 sdist
   in its separate pinned environment against the same corpus, request manifest
   and disposable databases. Compare observable HTTP/storage/Python results and
   decoded images using the existing comparator. Keep accepted RW deviations
   narrow and explicit; do not weaken standalone assertions to match the oracle.

Use the pinned real corpus in `process/validation/reference/assets.json` and
`subsets.json`; verify checksums, source windows and transforms before testing.
Commit only small redistributable fixtures and hand-reviewed expected values.
Use exact decoded pixels for stable sample crops/masks/classes; for projection
cases with explained GDAL sampling differences, assert placement, alpha, seams
and a narrow documented visual bound under the rewrite policy. A global pixel
error threshold cannot hide a changed category or shifted boundary. Keep full
parents/BigTIFF in a checksum-verified cache. Synthetic microfixtures
are limited to otherwise hard-to-isolate NaN, all-invalid and transaction-fault
cases. Ordinary CI tests run offline after a distinct acquisition/cache step;
missing inputs fail the required job. If acquisition is unavailable, mark the
job failed/incomplete, never silently replace the raster or skip the case.

## Critical-path matrix

Each row below needs both happy and failure/boundary cases. The named existing
validators are starting material, not current CI passes.

| Contracts | Standalone end-to-end assertions through RCFP | Transitional differential source |
| --- | --- | --- |
| C01–04, C11 | Create a known multi-key catalog; assert ordered keys/descriptions, filters, list parsing, sorting, paging, GET/POST metadata, colormap legends, methods, CORS, docs/spec, invalid URL/body/version outcomes. | Port `p04_validate.py` request matrix; compare JSON types/order and meaningful headers/status. |
| C05–10 | Render singleband/RGB/compute tile and preview from real imagery, DEM and categorical rasters. Assert decoded RGBA, masks, classes, geographic placement, seam pixels, dimensions, transforms, stretch/palette results, parser operations and invalid options. Test native projected/geographic inputs, nodata/valid zero/alpha, empty/outside/high-zoom and dateline/polar cases. | Reuse `p04_validate.py`, `p04_edges.py`, `p04_expressions.py`, `p08_geography.py`; apply only RW-logged image bounds. |
| C12–13, C36 | Read the same raster locally and by actual loopback HTTP range; assert pixels, request ranges/cache/failure recovery and full/sampled/chunked statistics, hull, invalid/constant values. Run full WorldCover under an RSS budget and actual >4 GiB TIFF offsets in stress lane. | Existing P02/P05/P08 corpus results; compare typed arrays/masks and metadata, not compressed PNG bytes. |
| C14–20 | Exercise SQLite and disposable MySQL 8.4/PostgreSQL 16: create/insert/list/update/delete, ordering/collation, lazy metadata races, transaction rollback/savepoints, reconnect, credentials/TLS, legacy migrations and read-only failures. Exercise S3 SQLite snapshot through a socket emulator with TTL/refresh/failure/leases. | Python↔Rust reads and writes on the same schemas; historical migrations and Python read-back of Rust-written rows. Real AWS remains a separate unqualified claim. |
| C21–26 | Run `serve`, `ingest`, `optimize-rasters`, `connect`, `migrate` as subprocesses on disposable data; assert files/rows/pixels/overviews/masks and exit behavior. Test settings defaults, precedence, aliases and bad values. | Adapt P05/P06/P07 validators; compare workflows and original Python outputs where shared behavior applies. |
| C27–28 | Install/import `rcfp`; exercise exported settings/data APIs, types/exceptions, contexts/futures, WSGI, CLI server and native HTTP against real raster/SQL data. Enumerate intentionally absent legacy exports/Flask API as explicit product gaps. | Run applicable frozen upstream tests in a controlled compatibility harness, plus direct pairwise public behavior. Do not use a fake `terracotta` import in the installed product. |
| C29–33 | Drive bounded queue/cache/timeout/disconnect/recovery; check metrics/logging/tracing output and secret redaction. Install each final wheel in an empty environment; render after Rasterio/NumPy/Pillow removal. Exercise native/container/official Lambda image. Browser test selects data and RGB, opens metadata and checks decoded tile responses with an offline basemap. | Existing P07/P08 resource, package and browser checks; compare responses. Actual AWS/basemap claims remain outside emulated evidence. |
| C34–35 | Verify corpus provenance and artifact hashes; migration/rollback workflow leaves data readable and rendered identically. Record benchmark/resource results in a separate qualification lane, with equivalent cache/CPU settings. | Terracotta rollback read-back and paired performance measurements; speed claims are not correctness gates. |

## CI lanes and release policy

| Lane | Trigger and runner | Required result |
| --- | --- | --- |
| Fast standalone | Every PR, main and release reuse; Linux x86_64 | Rust unit/integration plus installed-wheel public HTTP/Python/WSGI/CLI and SQLite real-corpus matrix. All declared case IDs present; no skips. Run with `python -O` once or use explicit checks so assertions cannot disappear under optimization. |
| Services standalone | Every PR, main and release reuse; Linux x86_64 with disposable MySQL/PostgreSQL and loopback range/S3 emulator | Real engine transactions/migrations, remote reads and failure paths pass. Services are pinned, health-checked, isolated per job and cleaned up. No backend is omitted when unavailable. |
| Differential | Every PR, main and release reuse; Linux x86_64 | Frozen sdist hash/environment verified; a representative all-family request/workflow matrix passes, with explicit accepted deviations. Full legacy applicable suite and expanded 526-case HTTP matrix run on main/release (or every PR if measured runtime permits); publish case counts and excluded oracle-only failures separately. |
| Wheel/platform | Every PR, main and release reuse; existing five OS/architecture cells | Install the exact repaired wheel artifact and run the same portable standalone contract suite, including known decoded pixels, RGB, compute, metadata, storage and CLI/WSGI. Keep an OS-specific smoke only for native library loading and platform quirks. Linux additionally runs official Lambda runtime image; preserve archive hash between test and publish. |
| Stress/browser | Scheduled and before release; Linux | Full parent and physical BigTIFF offsets, bounded RSS/FD, concurrency/soak/recovery, Chromium workflows and inspected image artifacts. A failed scheduled run is investigated before a release; the release workflow itself invokes the required stress/browser qualification or verifies a successful run for the exact commit and artifacts. |

Add one reusable correctness workflow and make `release.yml` depend on it in
addition to `wheels.yml` and `platforms.yml` **before another public tag**.
`publish` continues to receive only artifacts built and tested in that run.
Branch protection should require the standalone, services, differential and
five wheel jobs. Do not publish on a green packaging smoke while the required
correctness workflow is absent, skipped or failing. External AWS and Mapbox
checks are separately tracked P08 release limitations; local emulators must not
clear them. Any intended full-parity release claim still needs those checks or
an explicit scope decision.

## Implementation sequence and exit checks

1. **Freeze the gate and inventory.** Add a CI case manifest keyed to C01–C36
   and the 386 records, listing test ID, expected source, tier, platform and
   applicability. Audit every legacy script for old imports, hard-coded binary
   names/ports, stale fixture paths and writes into tracked evidence. Add a
   release dependency on the new correctness workflow as soon as it exists;
   fail closed until its required jobs are implemented. Exit: manifest has no
   unexplained critical-family holes; a deliberately failing check blocks a
   test release run without publishing.
2. **Build the portable standalone harness.** Create isolated temporary DBs,
   ports, processes and output directories; verify corpus hashes; load reviewed
   expected JSON/pixel/metadata values; report JUnit plus JSON with case IDs,
   fixture hashes, build/wheel hash, pass/fail/skip and timings. Run against the
   **installed wheel**, not a source-tree import. Put portable cases in a
   package-independent test directory included by CI/source archives, so
   testing does not depend on ignored historical artifacts. Exit: clean Linux
   venv run covers C01–11/C15/C21–22/C26–28/C31 and a controlled bad-pixel,
   wrong-status and missing-fixture mutation each fails for the right reason.
3. **Fill data and failure paths.** Add real SQL, HTTP-range/S3 emulator,
   metadata/optimization, migration and resource test modules; split fast and
   stress inputs by declared resource budget. Assert read-back through both
   public Python and HTTP/CLI paths where useful. Exit: all critical rows above
   have executable standalone case IDs; both SQL engines and emulator run in
   clean CI; no service/environment skip is reported as pass.
4. **Rehome and repair differential validation.** Keep the oracle in
   `.cache/reference`, installed from the SHA-256-pinned sdist. Refactor
   `p04_validate.py`, `p07_validate.py`, `p08_qualify.py` and related runners to
   accept artifact paths and execute reference/candidate in separate processes.
   Preserve the upstream 526-pass/two-ANSI-failure and six boto-mock exclusions
   as *historical expectations to re-evaluate*, not hard-coded CI success.
   Exit: every differential case has matching input hashes, separate process
   provenance and a classified result; comparator mutation tests pass.
5. **Expand every wheel and connect release.** Run the portable installed-wheel
   suite on Linux x86_64/aarch64, macOS x86_64/arm64 and Windows x86_64. Resolve
   any GDAL/data/path differences with retained reproduction, then wire all
   required jobs into `release.yml`. Exit: each platform report names the tested
   wheel SHA-256 and all required case IDs; release assembly verifies exactly
   those hashes; a failing platform/correctness job prevents publish.
6. **Add scheduled depth and maintain it.** Wire physical BigTIFF, full-source
   COG, soak/failure, browser and benchmark/visual inspection jobs. Store bounded
   logs, reports and failing image pairs; link durable summaries in STATUS and
   COVERAGE. Every bug fix adds a standalone regression case; differential cases
   can retire only after their independent assertions and accepted-deviation
   bounds remain. Exit: scheduled and release-depth runs are reproducible from
   documented cache/bootstrap commands, and open external claims remain visible.

Do not take a raw pass count as a coverage claim. The implementation handoff
must show which family/record IDs are still missing, exact commands/results on
the current commit, fixture provenance, failure artifacts and remaining P08
blockers. Budget and shard sizes should be measured on hosted runners; keep the
gate comprehensive rather than silently trimming cases to fit a guessed time.
