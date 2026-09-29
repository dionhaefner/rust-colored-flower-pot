# Working on RCFP

## Product boundary

Keep rewrite plans, phase labels, qualification status, experiment notes,
reference-acquisition details, and development history inside `process/`.
Files outside `process/` must describe the current product and its supported
build, test, and release behavior. Do not expose process metadata through CLI
output, HTTP responses, Python APIs, package metadata, embedded assets, or logs.
Use product terms in source comments and tests; move historical explanation and
evidence here. Keep the source archive, wheel, and container context free of
`process/` contents. Before completing a change, search the product tree for
stale phase names, reference-workspace paths, and obsolete package identities.
Hatch VCS versioning is part of the supported package build.

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

The Python reference is the checksum-pinned Terracotta 0.10.1 PyPI source
distribution, published from commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`.
Fetch it into the ignored `.cache/reference` directory with
`process/validation/tools/bootstrap-reference.sh`; do not vendor it into this source tree.
Keep reference environments, generated fixtures, and rewrite experiments in
ignored workspace or temporary storage.

Keep STATUS.md as the progress summary and execution/COVERAGE.md as the feature
inventory. Link evidence from them; a skipped check is not a pass. Update the
discovery log for new findings, and use execution/DECISIONS.md for architectural
decisions. Templates and ready-to-use agent briefs are indexed in the README.

The harness itself does not authorize cloud spending, publishing, destructive
operations, or contacting others. Follow session authorization and tool
permissions. No extra approval is required by these documents for routine local
implementation, checks, or evidenced immaterial compatibility differences.
