# Implementation agent brief

Use this as the execution instruction alongside an assigned phase ID or bounded
task. The assignment determines scope; the policy determines compatibility.

---

You are implementing the Terracotta Rust rewrite in this repository. Complete
the assigned phase/task end to end. A plan or partial scaffold is not completion.

Read AGENTS.md, REWRITE.md, execution/WORKFLOW.md, execution/STATUS.md, your phase
in execution/PHASES.md, and execution/VALIDATION.md. Inspect relevant coverage,
discoveries, decisions, prior handoffs, Python source and tests. The initial
reference is the sibling `../terracotta` checkout; it is read-only. Confirm
the frozen reference and environment rather than using mutable checkout output
as an unexplained oracle.

For corpus/rendering work, read execution/DATASETS.md. The first phase must
acquire real community rasters and reproducible subsets, produce useful visual
references, and retain a large-raster stress case. Prefer real pixels and native
source characteristics; synthetic scenes cannot substitute for this evidence.

Preserve existing observable behavior and ordinary application appearance.
Keep GDAL, share core logic across adapters, retain the client, and preserve
storage/public contracts. Small proved rounding differences with no visible or
material semantic consequence are acceptable: retain evidence, log them, add a
narrow regression check, and move on. Log legacy bugs and opportunities for
later; fix regressions introduced by your port now.

Inspect actual repository state and prerequisites, then write a short executable
plan and implement it. Run focused checks, fix your failures, run relevant
integration checks, and satisfy the assigned acceptance criteria. Do not invent
test results, silently weaken tests, replace real-service evidence with mocks,
or count unsupported features as accepted numerical deviations.

Make routine reversible choices yourself. Do not ask whether to continue work
already assigned. Ask only for genuinely missing information/authority that
materially changes the result, with a specific recommendation and consequences.
Use the blocker procedure: reproduce, investigate, attempt evidence-based
remedies, document the missing condition, and continue independent work. Missing
cloud access is a reason to leave a cloud check pending, not to stop local work
or claim a deployed runtime passed.

Do not overwrite other work or modify the original reference checkout. Follow
session tool permissions and authorization for external actions. If delegation
is authorized, keep ownership explicit and integrate/test the results yourself.

Update execution/STATUS.md and execution/COVERAGE.md, REWRITE_LOG.md for
discoveries/deviations, and execution/DECISIONS.md for consequential choices.
End with a resumable handoff that states implemented scope, actual validation,
remaining limitations/blockers, preserved changes/processes, and the exact next
action. If multiple phases were assigned, continue through their ready tasks;
if one phase was assigned, finish it and identify its successor.

If no phase/task was specified, select the earliest ready incomplete phase from
STATUS.md and state that assumption before starting. Do not reset an in-progress
phase or take over another active owner's files without coordination.
