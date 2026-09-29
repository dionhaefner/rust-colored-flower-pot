# Execution status

## Current position

- [CI correctness-suite implementation](handoffs/ci-correctness-suite.md) adds
  53 required installed-wheel cases, 26 pinned Terracotta differential cases,
  live SQL and remote-storage checks, browser and HTTP-range tests, and scheduled
  large-raster/resource qualification. Local Linux contract, differential,
  SQLite, range, browser, BigTIFF, and short resource runs pass. The current
  unpushed wheel-builder and stress changes still need hosted qualification;
  Windows wheel build and the full installed-wheel matrix are unverified.
- Active rewrite records now live under `process/`; the root README describes the installable product. Source archives exclude the process tree, while the Cargo helper remains under `packaging/`. CI smokes and Rust tests use enduring `tests/` fixtures and generated databases; product code and release workflows have no dependency on the process tree. [Boundary handoff](handoffs/process-artifact-boundary.md), D-021.
- [CI/GDAL wheel pipeline](../CI.md) separates Ubuntu-built repaired Linux
  wheels from AL2023 Lambda bundles, retains five-platform builds and a
  CPython 3.12–3.14 installed-wheel matrix, and gates tag publishing on native,
  wheel and correctness workflows. PyPI trusted-publisher setup is external.
  [Handoff](handoffs/ci-testing-stack.md).
- P08 implementation is locally qualified, but post-qualification investigation
  found unequal aggregate GDAL cache budgets in the benchmark (RW-043). A
  corrected full benchmark remains local follow-up work; external gates remain. [Handoff](handoffs/P08.md), [evidence](../validation/evidence/p08/README.md).
- The final candidate repairs native worker shutdown (RW-039/D-011) and dateline
  preview framing/wrapping (RW-041). All 386 detailed inventory records are
  reconciled: 336 verified, 43 accepted deviations, seven partial. Platform
  qualification is limited to Linux x86_64 across the inventory.
- Source `pip install .` now builds the `rcfp` distribution, exposes `import rcfp`, and registers `rcfp` on local Linux/CPython 3.12; [identity evidence](../validation/evidence/p08/rcfp-identity/README.md).
- Native Linux x86_64 / CPython 3.12 wheel, Debian container and official AL2023
  runtime-image rendering pass. Canonical archive installation/rendering and
  repeatable archive checksums pass. Nothing was published, tagged or deployed.
- The frozen oracle now comes from the SHA-256-pinned Terracotta 0.10.1 PyPI
  source release, published from commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`.
  The tracked `python/terracotta` copy was removed at the user's request.
  [External-reference evidence](../validation/evidence/p08/external-reference/README.md).

## Final local results

Twenty Rust unit tests and SQL/remote integration, fmt and Clippy pass. Final
HTTP 526, packaged adapters 46, public integration 27, settings 46, remote socket
emulator 13, preparation 329, real SQL engines 123 and migrations 19 pass.
The unchanged applicable Python suite has 526 passes, two reproduced oracle ANSI
failures (RW-036), zero skips and six explicitly excluded in-process boto mocks
(RW-037). Counts overlap; failures and exclusions are not passes.

P08 adds 30 exact polar/dateline rendering cases, 96 geographic metadata checks
with explained input-specific float32 reduction RW-042, nine physical BigTIFF
checks beyond 4 GiB, five rollback workflows, 20 owner lifetimes, disconnect/drain
and failure recovery, a 180-second soak and 200 clean AL2023 cold process exits.
Chromium's unchanged client passes all 192 backend responses with queue 64 and
retained inspected singleband/RGB/metadata views on an offline basemap.

The fair benchmark has 24 matched runs (three trials, cache off/on, local/range,
six workloads, clients 1/3 and equal three-core affinity). Performance is mixed:
several tile paths improve, while RGB and geographic DEM previews are slower.
No universal speedup is claimed. [Measurements and limits](../validation/evidence/p08/PERFORMANCE.md).

## Phase ledger

| Phase                                                | State       | Owner | Plan / handoff / evidence                                                                          | Pending exit checks                                                                                                                        |
| ---------------------------------------------------- | ----------- | ----- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| P00 Real corpus, reference and validation foundation | complete    | Codex | [plan](plans/P00.md), [handoff](handoffs/P00.md), [evidence](../validation/evidence/p00/README.md) | None; physical BigTIFF is an explicit later C36 gap                                                                                        |
| P01 SQLite singleband vertical slice                 | complete    | Codex | [plan](plans/P01.md), [handoff](handoffs/P01.md), [evidence](../validation/evidence/p01/README.md) | None                                                                                                                                       |
| P02 RGB, remote reads, bounded execution             | complete    | Codex | [plan](plans/P02.md), [handoff](handoffs/P02.md), [evidence](../validation/evidence/p02/README.md) | None locally; real S3/custom endpoint and physical BigTIFF remain explicit release gaps                                                    |
| P03 Distribution and Python feasibility              | complete    | Codex | [plan](plans/P03.md), [handoff](handoffs/P03.md), [evidence](../validation/evidence/p03/README.md) | None locally; actual AWS invocation requires separate deployment/spend authorization, and non-x86_64/non-Linux targets remain P07/P08 gaps |
| P04 Rendering and HTTP completion                    | complete    | Codex | [plan](plans/P04.md), [handoff](handoffs/P04.md), [evidence](../validation/evidence/p04/README.md) | None; narrow float32 deviation RW-019 accepted with exact real-raster image evidence                                                       |
| P05 Metadata, ingestion, optimization                | complete    | Codex | [plan](plans/P05.md), [handoff](handoffs/P05.md), [evidence](../validation/evidence/p05/README.md) | None for P05; narrow moment/hull roundoff accepted as RW-024/RW-025                                                                        |
| P06 Storage backends and migrations                  | complete    | Codex | [plan](plans/P06.md), [handoff](handoffs/P06.md), [evidence](../validation/evidence/p06/README.md) | None locally; real S3/role/SSO service credentials remain explicitly pending                                                               |
| P07 Integration and operational completion           | complete    | Codex | [plan](plans/P07.md), [handoff](handoffs/P07.md), [evidence](../validation/evidence/p07/README.md) | None in revised scope; nonlocal CI results explicitly deferred by user (D-010)                                                             |
| P08 Release qualification                            | in-progress | Codex | [plan](plans/P08.md), [handoff](handoffs/P08.md), [evidence](../validation/evidence/p08/README.md) | RW-043 corrected aggregate-cache benchmark; B08-PLATFORMS, B08-AWS, B08-BASEMAP                                                            |

## External blockers and exact next actions

| ID            | Missing condition                                                                            | Resume action                                                                                                                                                                                                        |
| ------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B08-PLATFORMS | User deferred platform execution (D-010)                                                     | When resumed, run [Platform qualification](../../.github/workflows/platforms.yml) for Linux/Lambda arm64, macOS Intel/arm64 and Windows x86_64; retain actual uploaded results. Prepared CI is not passing evidence. |
| B08-AWS       | Authorized test account, region, disposable resources/credentials and deployment/spend scope | Execute real S3/custom endpoint, role/SSO refresh, actual Lambda binary-response and X-Ray ingestion checks; retain service evidence and cleanup. Local emulators/runtime images do not clear these claims.          |
| B08-BASEMAP   | Intended authorized external Mapbox/token deployment                                         | Exercise unchanged browser assets with the external basemap and retain the result. Local exploration is qualified only with the offline background.                                                                  |

No new permission checkpoint is imposed by the harness. These are unavailable
external prerequisites or user-deferred work, not failing local implementation.
Mark P08 complete after required gate evidence or an explicit accepted scope
change. No successor implementation phase is defined; publishing remains a
separately authorized action.

## Retained state and last handoff

[Resume P08](handoffs/P08.md) from the final reports and artifact hashes. Candidate:
`process/validation/artifacts/p08-candidate` (native tarball, wheel, Lambda ZIP,
source/evidence archive, manifest and SHA256SUMS); repeat preparation:
`p08-candidate-repeat`. [Release manifest](../validation/evidence/p08/release-manifest.json),
[reproducibility](../validation/evidence/p08/reproducibility.json),
[source/runtime manifest](../validation/evidence/p08/run-manifest.json).

Underlying final outputs: `p08-final`, `p08-final-python`, `p08-final-wheels`,
`p08-final-lambda`; installed environment `p08-final-install`; native target cache
`p07-native-target` contains final P08 code. Earlier `p07-final` and
`p08-*-before-dateline` builds are diagnostic history, not the current candidate.
The pinned corpus, reusable build caches/images and physical 7.1 GB BigTIFF remain.

Disposable SQL containers and volumes were removed. No phase daemon is left
intentionally running. The repository was initially wholly untracked. Initial commit
`812db1aadcbca93d77101aa6507b47c92f683ef1` appeared during qualification; it was
not created by this agent. Final qualification updates remain in the working
tree. Runtime source fingerprints still match the tested build. The sibling
Python checkout was not modified.

## Post-qualification discovery

[RW-043 diagnostic](../validation/evidence/p08/bottleneck-followup/README.md)
identifies GDAL cache pressure as the dominant preview regression. Prior
performance figures compare 32 MiB native with 96 MiB aggregate Python GDAL
cache. The full corrected matrix is pending; sealed artifacts and source archives
predate this addendum. No runtime implementation changed during this diagnostic.

## User-facing navigation baseline

The [map-navigation follow-up](../validation/evidence/p08/navigation/README.md)
now measures first views and new-tile pan completion for 1/4/8 sessions at matched
96 MiB aggregate GDAL and 64 MiB logical raster-cache budgets. All 36 runs and
4,992 requests pass, with exact images and fully opaque real-data tiles. Native
pan p50 is 1.15×/1.28× faster for singleband/RGB with one session and 1.92×/1.50×
at eight sessions. This is local server tile delivery, not browser animation or
WAN latency. Use it as the primary performance baseline for map browsing;
older expression/repeated-tile results are secondary diagnostics. No runtime
optimization or resealing was performed in this measurement task.

## P08 map-navigation 10× addendum (2026-09-27)

[Scoped Rust fast-path evidence](../validation/evidence/p08/map10/README.md) supersedes the
old navigation baseline **only** for identical Web Mercator Sentinel-2 COGs,
UInt16 opaque tile interiors, bilinear reprojection and average resampling.
Both Python and Rust used the same derived real imagery. Three isolated
1/4/8-user singleband/RGB matrices, 4,992 requests and 412 distinct decoded
images pass exactly with equal three-core affinity and 160 MiB logical total
cache. Median paired pan speedups are 11.84×/11.56× (one user),
15.86×/12.17× (four), 34.19×/27.32× (eight); one of 18 paired trial cells is
9.62×. First views are about 3.6–6.0×. In this earlier result the
original UTM COGs still used the exact, slower GDAL fallback; the later UTM
addendum below qualifies a separate opt-in path. Neither result covers
arbitrary rasters.

This source build SHA-256 is `0b27378a546962426e0e05b85136a5d3d129f0d2baa48616fb04274fd70d8ffe`.
Twenty Rust unit tests, Clippy and fmt pass. P08 packaged archives predate
these changes; platform, remote service, browser and release gates remain as
stated above. D-012 and RW-045 record the scope and rejected experiments.
[Resumable addendum handoff](handoffs/map-performance-10x.md).

## P08 original-UTM map-navigation addendum (2026-09-28)

[Original COG evidence](../validation/evidence/p08/map10-utm/README.md) extends the
map-pan 10× result to the unmodified EPSG:32632 Sentinel-2 red/green/blue COGs
under `TC_RUST_UTM_FAST=1` and `TC_PREFETCH_TILE_RING=1`. Three isolated paired
1/4/8-user singleband/RGB trials, equal three-core affinity and 160 MiB aggregate
logical cache, 4,992 successful requests, 412 real fully opaque URLs. Three-trial
median pan p50 gains are 24.66×/30.94× (one user), 23.94×/16.09× (four) and
33.44×/13.77× (eight); every one of the 18 paired trial ratios is above 10×.
The final binary SHA-256 is `f7843c2fffb16db78c13bac677870d578eca83a040be81ad5276487fdc83effc`.

The UTM path has an accepted visual difference (RW-046/D-013): all 412 alpha
masks match, each image p99 RGB channel difference is at most 4/255, the largest
per-image RGB mean absolute difference is 0.61/255, and ordinary-scale worst-case
RGB/singleband pairs and 2×2 seam mosaics were inspected with no visible change.
The strict decoded-hash report remains `passed=false`; the separate
[operation-specific qualification](../validation/evidence/p08/map10-utm/summary.json)
passes. Exact general GDAL fallback is verified on zoom-12/14 and partial or
transparent edge tiles. This performance scope is near-native, fully valid
EPSG:32632 tile interiors. First views are 3–5× and eight-user RGB pan p95 is
about 7.4×; broader CRSs, overview-scale/edge fast paths, remote traffic,
platform builds and browser paint remain outside this measurement. Rust fmt,
Clippy `-D warnings` and 20 unit tests pass. The previous Web Mercator result
remains separate. [Resumable handoff](handoffs/map-performance-utm-10x.md).

## P08 UTM geometry-cache follow-up (2026-09-28)

The [latest original-UTM evidence](../validation/evidence/p08/map10-utm-geometry/README.md)
reuses the worker-owned suggested-geometry cache in the opt-in UTM path. An
alternating three-trial Rust A/B confirms identical decoded images for 412 URLs
and eight-user RGB pan p50 79.57→52.23 ms (1.52×); four-user RGB improves
30.20→22.70 ms (1.33×). Single-user RGB has no consistent A/B gain. Against
the frozen Python service, the current candidate's three-trial median pan gains
are 24.56×/27.19× (one-user singleband/RGB), 29.00×/21.08× (four), and
37.49×/23.09× (eight), with every paired trial still above 10×. First views
and the accepted RW-046 visual difference retain their earlier scope.

Candidate SHA-256 is `3fa0b076f977a62a97e6247aa64bb0a64aecbe6356971f26c59a7cc861536ba5`.
The earlier [UTM evidence](../validation/evidence/p08/map10-utm/README.md) is the
pre-cache baseline, not the current best measurement. Six exact fallback cases,
20 Rust unit tests, fmt and strict Clippy pass. Two/six raster-worker probes on
the same three cores were slower for loaded RGB; keep three workers. Release
archives still predate this source. [Handoff](handoffs/map-performance-utm-geometry.md).

## P08 UTM first-view diagnosis (2026-09-28)

[First-view evidence](../validation/evidence/p08/map10-utm-first-view/README.md)
explains why the established 10× pan result does not extend to a complete fresh
viewport. The first 12 tiles (36 RGB bands) encounter cold worker coordinate
setup and source COG blocks; later pans expose only three or four new tiles and
benefit from cache and prefetch. Three-trial first-view gains remain 3.06–6.09×
across singleband/RGB and one/four/eight users. Temporary stage profiling found
about 35 ms coordinate setup and 25 ms source-window/block cost for each of the
first three completed RGB band reads, versus submillisecond warm medians for
those stages. A same-binary, three-trial prefetch-on/off A/B found no robust
first-view improvement when disabled and a strong one-user pan regression, so
prefetch remains enabled. No production raster change or new first-view speed
claim resulted; the temporary profiling hooks were removed. [Handoff](handoffs/map-performance-utm-first-view.md).

## P08 UTM first-view optimization trials (2026-09-28)

[Follow-up A/B evidence](../validation/evidence/p08/map10-utm-first-view-followup/README.md)
rejects two proposed low-level fixes for cold first-view latency. Concurrent
misses for the same source block were rare in RGB (one wait in the one-user
whole run); a singleflight candidate was exact for 412 images but did not
reliably improve the first viewport. Reusing a GDAL transformer per worker and
source was also image exact, yet nine alternating RGB trials found no reliable
first-view improvement (one-user median 153.57→156.07 ms, eight-user
711.95→725.46 ms). Both experiments were removed. A clean release rebuild
restored the preceding qualified SHA-256
`3fa0b076f977a62a97e6247aa64bb0a64aecbe6356971f26c59a7cc861536ba5`.
The established scoped pan result and compatibility rule remain in force.
[Handoff](handoffs/map-performance-utm-first-view-followup.md).

## Source pip installation and `rcfp` CLI follow-up (2026-09-28)

This was the first source-wheel result. It is superseded by the RCFP-only Python distribution described below. The repository root builds through `pip install .`, compiling/staging the Rust core and CPython extension. Local CPython 3.12/Linux x86_64 wheel build, direct source install, fresh-wheel CLI smoke, staged-wheel regression, real-catalog native identity read and HTTP `/keys` serving pass. [Evidence](../validation/evidence/p08/source-pip-rcfp/README.md), [handoff](handoffs/source-pip-rcfp.md), RW-050/D-015. Online build isolation/dependency resolution and nonlocal platforms were not tested; prior sealed P08 artifacts were not rebuilt. P08 external release gates and the existing performance follow-ups remain unchanged.

## RCFP Python identity correction (2026-09-28; prior source layout)

This records the layout before the external-reference change below. The user corrected the source boundary: `python/terracotta` remains the unchanged original reference, while the current installable package is `python/rcfp`. Cargo now builds the `rcfp` executable and `rcfp_core` library. The Linux/CPython 3.12 wheel contains only `rcfp`, its native extension, runtime and CLI; a direct source `pip install` and exact real-raster/WSGI/CLI smoke pass. The old `python/terracotta_rust` adapter was removed after explicit user approval; the original `python/terracotta` remains untouched. The earlier RCFP wheel evidence predates this correction and does not prove full legacy driver/Flask compatibility under `rcfp`. [Current evidence](../validation/evidence/p08/rcfp-source-boundary/README.md), [handoff](handoffs/rcfp-source-boundary.md), RW-053/D-017. Normal local isolated `pip install .` with Rasterio 1.4.4/GDAL 3.10.3 passes; nonlocal platforms and release repair remain unqualified.

## External Terracotta reference (2026-09-28)

The user asked to drop the original Terracotta source from RCFP's tree. The
tracked `python/terracotta` directory was removed. The reference bootstrap now
fetches the pinned 0.10.1 source release from PyPI and verifies SHA-256 before
extracting it into ignored `.cache/reference`; a local copy of that archive can
be supplied for offline bootstrapping. The release attests the same frozen
commit, and 309 application files plus 35 tests match the existing snapshot
byte for byte; `_version.py` is release generated. RCFP's 16 retained browser
asset files also match the release. [Evidence](../validation/evidence/p08/external-reference/README.md),
[handoff](handoffs/external-reference.md), RW-055/D-019.
