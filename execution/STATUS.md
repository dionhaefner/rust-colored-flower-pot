# Execution status

## Current position

- P07 is complete within the user-authorized local/CI-preparation scope (D-010).
  Legacy Python/Flask/WSGI/CLI/settings/client integration, owned native sessions,
  public contexts/futures/types, profiling/logging, packages and operator docs are
  implemented. [Handoff](handoffs/P07.md), [evidence](../validation/evidence/p07/README.md).
- P08 release qualification is in progress (Codex); [plan](plans/P08.md). The user explicitly deferred nonlocal
  platform runs to prepared GitHub Actions. C32 remains partial: CI being ready
  is not evidence that macOS, Windows or arm64 passed.
- Frozen source remains `6db38d5873cde5ebabe47338a1f61ddc788eada6`, exported
  independently of the sibling's modified tree. Earlier real-corpus, rendering,
  metadata/ingestion and storage evidence remains linked in COVERAGE.
- Final Linux x86_64 native/CPython wheel, Debian container and official AL2023
  runtime-image load/render pass with exact real-raster RGBA; maximum glibc 2.34.
  Actual AWS/S3/X-Ray services remain unqualified.

## P08 qualification

The final combined suite has completed, including the repaired native worker
shutdown (RW-039/D-011), exact dateline previews (RW-041), 180-second soak and
200 cold official-runtime exits. The isolated benchmark, strict browser check,
geographic metadata extension and canonical archive checks are being finalized.
[Plan](plans/P08.md), [evidence](../validation/evidence/p08/README.md).
No full-parity or cloud/platform completion claim is made.

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
| P08 Release qualification | in-progress | Codex | [plan](plans/P08.md) | Combined qualification underway; external checks remain explicit |

## Blockers and pending questions

No outstanding user question or local P07 blocker. The user requested GitHub
Actions setup and deferred platform execution. [The workflow](../.github/workflows/platforms.yml)
is prepared and linted; its Linux staging/Lambda build paths were tested locally.
Linux/Lambda arm64, macOS Intel/arm64 and Windows x86_64 remain unrun.
Real S3/role/SSO services, actual AWS Lambda/X-Ray delivery, physical multi-GiB
BigTIFF offsets, broader geographic/alpha stress, soak and fair performance
qualification remain explicit P08 release checks. Browser exploration is now
verified with an offline plain basemap; external basemap/token integration is not.

## Last handoff

[P07 completed](handoffs/P07.md): final HTTP 526/526; packaged adapters 46/46;
settings 46/46; real-corpus public integration 27/27; engine/context/migration
121/121; public remote socket-emulator 13/13. Nineteen Rust unit tests, rustfmt,
Clippy, owner-release/disconnect and final failure/recovery checks pass.

The final applicable unchanged Python suite has 526 passes and two ANSI-capture
failures reproduced in the frozen oracle (RW-036), with no skips. Its six remote
in-process boto-mock cases are explicitly excluded and replaced by labeled
socket-emulator evidence; two real eager-error defects are repaired (RW-037).
No excluded case or oracle failure is called a pass.

Final artifacts: `validation/artifacts/p07-final`, `p07-final-python`,
`p07-final-wheels`, and `p07-final-lambda/terracotta-lambda.zip`. Use these rather
than older `p07-release*` intermediates. Native target cache: `p07-native-target`.
Fresh installed-wheel environment: `p07-install`. The CI-built Linux/Lambda zip
also passed local official-runtime rendering; other CI targets await execution.

Disposable SQL containers/volumes were removed, and no phase daemon is
intentionally left running. Reusable artifacts/images/caches remain. Workspace
changes remain uncommitted from the initially untracked state. Start P08 from
D-009/D-010, RW-032–038, the evidence index and the handoff's exact next actions.
