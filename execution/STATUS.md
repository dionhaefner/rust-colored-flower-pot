# Execution status

## Current position

- P08 implementation is locally qualified, but post-qualification investigation
  found unequal aggregate GDAL cache budgets in the benchmark (RW-043). A
  corrected full benchmark remains local follow-up work; external gates remain. [Handoff](handoffs/P08.md), [evidence](../validation/evidence/p08/README.md).
- The final candidate repairs native worker shutdown (RW-039/D-011) and dateline
  preview framing/wrapping (RW-041). All 386 detailed inventory records are
  reconciled: 336 verified, 43 accepted deviations, seven partial. Platform
  qualification is limited to Linux x86_64 across the inventory.
- Native Linux x86_64 / CPython 3.12 wheel, Debian container and official AL2023
  runtime-image rendering pass. Canonical archive installation/rendering and
  repeatable archive checksums pass. Nothing was published, tagged or deployed.
- Oracle commit `6db38d5873cde5ebabe47338a1f61ddc788eada6` is unchanged; all 412
  tracked blobs were verified against commit objects. The sibling is read-only.

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

| Phase | State | Owner | Plan / handoff / evidence | Pending exit checks |
| --- | --- | --- | --- | --- |
| P00 Real corpus, reference and validation foundation | complete | Codex | [plan](plans/P00.md), [handoff](handoffs/P00.md), [evidence](../validation/evidence/p00/README.md) | None; physical BigTIFF is an explicit later C36 gap |
| P01 SQLite singleband vertical slice | complete | Codex | [plan](plans/P01.md), [handoff](handoffs/P01.md), [evidence](../validation/evidence/p01/README.md) | None |
| P02 RGB, remote reads, bounded execution | complete | Codex | [plan](plans/P02.md), [handoff](handoffs/P02.md), [evidence](../validation/evidence/p02/README.md) | None locally; real S3/custom endpoint and physical BigTIFF remain explicit release gaps |
| P03 Distribution and Python feasibility | complete | Codex | [plan](plans/P03.md), [handoff](handoffs/P03.md), [evidence](../validation/evidence/p03/README.md) | None locally; actual AWS invocation requires separate deployment/spend authorization, and non-x86_64/non-Linux targets remain P07/P08 gaps |
| P04 Rendering and HTTP completion | complete | Codex | [plan](plans/P04.md), [handoff](handoffs/P04.md), [evidence](../validation/evidence/p04/README.md) | None; narrow float32 deviation RW-019 accepted with exact real-raster image evidence |
| P05 Metadata, ingestion, optimization | complete | Codex | [plan](plans/P05.md), [handoff](handoffs/P05.md), [evidence](../validation/evidence/p05/README.md) | None for P05; narrow moment/hull roundoff accepted as RW-024/RW-025 |
| P06 Storage backends and migrations | complete | Codex | [plan](plans/P06.md), [handoff](handoffs/P06.md), [evidence](../validation/evidence/p06/README.md) | None locally; real S3/role/SSO service credentials remain explicitly pending |
| P07 Integration and operational completion | complete | Codex | [plan](plans/P07.md), [handoff](handoffs/P07.md), [evidence](../validation/evidence/p07/README.md) | None in revised scope; nonlocal CI results explicitly deferred by user (D-010) |
| P08 Release qualification | in-progress | Codex | [plan](plans/P08.md), [handoff](handoffs/P08.md), [evidence](../validation/evidence/p08/README.md) | RW-043 corrected aggregate-cache benchmark; B08-PLATFORMS, B08-AWS, B08-BASEMAP |

## External blockers and exact next actions

| ID | Missing condition | Resume action |
| --- | --- | --- |
| B08-PLATFORMS | User deferred platform execution (D-010) | When resumed, run [Platform qualification](../.github/workflows/platforms.yml) for Linux/Lambda arm64, macOS Intel/arm64 and Windows x86_64; retain actual uploaded results. Prepared CI is not passing evidence. |
| B08-AWS | Authorized test account, region, disposable resources/credentials and deployment/spend scope | Execute real S3/custom endpoint, role/SSO refresh, actual Lambda binary-response and X-Ray ingestion checks; retain service evidence and cleanup. Local emulators/runtime images do not clear these claims. |
| B08-BASEMAP | Intended authorized external Mapbox/token deployment | Exercise unchanged browser assets with the external basemap and retain the result. Local exploration is qualified only with the offline background. |

No new permission checkpoint is imposed by the harness. These are unavailable
external prerequisites or user-deferred work, not failing local implementation.
Mark P08 complete after required gate evidence or an explicit accepted scope
change. No successor implementation phase is defined; publishing remains a
separately authorized action.

## Retained state and last handoff

[Resume P08](handoffs/P08.md) from the final reports and artifact hashes. Candidate:
`validation/artifacts/p08-candidate` (native tarball, wheel, Lambda ZIP,
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
