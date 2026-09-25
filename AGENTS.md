# Working on Terracotta

For Rust rewrite work, read [REWRITE.md](REWRITE.md) before making design or
implementation decisions. It defines the objectives, compatibility policy,
starting architecture, and completion criteria.

Maintain [REWRITE_LOG.md](REWRITE_LOG.md) as discoveries arise. Record legacy
bugs, inconsistencies, opportunities, and accepted compatibility differences;
do not silently fix legacy behavior or expand the initial port's scope.

## Execution harness

Read [execution/WORKFLOW.md](execution/WORKFLOW.md),
[execution/STATUS.md](execution/STATUS.md), your phase in
[execution/PHASES.md](execution/PHASES.md), and
[execution/VALIDATION.md](execution/VALIDATION.md) before implementation.
For reference-data and rendering work, also read
[execution/DATASETS.md](execution/DATASETS.md). Prefer reproducible subsets of
real rasters; synthetic images are reserved for narrowly justified edge cases.

Finish the authorized phase or task end to end: inspect, plan, implement,
validate, repair failures, and leave a resumable handoff. Do not stop after
writing a plan or ask whether to perform work already included in the assignment.
Use the clarification/blocker procedure in WORKFLOW.md; continue independent
work while waiting for genuinely required input.

The Python reference is a separate repository, initially `../terracotta`, at
commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`. Treat it as read-only; put
reference environments, generated fixtures, and rewrite experiments here or in
temporary storage. Do not assume its current working tree is the frozen oracle.

Keep STATUS.md as the progress summary and execution/COVERAGE.md as the feature
inventory. Link evidence from them; a skipped check is not a pass. Update the
discovery log for new findings, and use execution/DECISIONS.md for architectural
decisions. Templates and ready-to-use agent briefs are indexed in the README.

The harness itself does not authorize cloud spending, publishing, destructive
operations, or contacting others. Follow session authorization and tool
permissions. No extra approval is required by these documents for routine local
implementation, checks, or evidenced immaterial compatibility differences.
