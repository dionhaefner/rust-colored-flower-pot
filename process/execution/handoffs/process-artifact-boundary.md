# Process and artifact boundary handoff

Date: 2026-09-28. Scope: repository layout only; no runtime behavior change intended.

- Moved `REWRITE.md`, `REWRITE_LOG.md`, `execution/`, and `validation/` to `process/`. The root README now gives installation and product information; `process/README.md` owns the rewrite map and resume instructions. P08 candidate notes and the local archive generator also moved from `packaging/` into `process/`.
- Moved the Cargo helper to `packaging/cargo.sh` so the wheel and source build no longer need `process/`. Moved the local review archive generator to `process/tools/prepare-candidate.py`. Build defaults use ignored `dist/` or `build/`. The source manifest prunes `process/` and excludes the root agent entry point and CI design note. Operator, rollback and third-party notice documents remain with the product and no longer require rewrite-only commands.
- Updated references in CI, Rust tests, packaging, validation scripts, docs, and ignored output paths. Existing ignored validation artifacts moved with the tree. Preserved all pre-existing worktree edits and untracked evidence.
- Checks: Rust formatting and 20 offline Rust tests passed; 19 comparator tests passed in `.venv-reference`; Python compile and shell syntax passed; source archive built with `packaging/cargo.sh`, product docs, and no `process/`, `AGENTS.md`, or CI design note; `cargo check --locked --offline` passed from the extracted archive. A first comparator attempt with system Python could not run because it lacks pytest; the pinned environment passed.

Update 2026-09-29: the remaining migration is complete. The platform and Lambda
smokes live under `tests/`, with a committed source manifest and golden PNG.
Rust expression/image fixtures live under `tests/fixtures`; SQLite and remote
storage tests create their own disposable databases. CI writes to ignored
`build/ci/`, and its workflows and `process/CI.md` have no process-tree
paths. The root agent file moved into `process/`. The active qualification
records remain here and can be deleted as one unit when obsolete.

Verification on 2026-09-29: 19 Rust unit tests, the socket-based remote SQLite
test, 19 transitional comparator tests, and the extracted native real-raster
smoke passed. The source archive excludes `process/` and its extracted Rust unit
suite passes. Python and shell syntax, Rust formatting, local Markdown links,
and diff whitespace checks pass. The Docker Lambda image and five hosted CI
platforms were not run in this local migration.
