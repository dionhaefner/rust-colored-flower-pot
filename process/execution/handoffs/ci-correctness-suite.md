# CI correctness-suite planning handoff

Task: answer the request for a substantial correctness-test and CI plan.
The plan is [ci-correctness-suite.md](../plans/ci-correctness-suite.md).
No workflow or test implementation was part of this planning task and
no new test result is claimed.

Inspected the current five-platform native/wheel workflows and tag release
gate, compact `p07_platform_smoke.py`, historical P04/P05/P06/P07/P08
validators, frozen reference provenance, DATASETS/VALIDATION guidance and
C01–C36 coverage. Existing broad validators have local historical evidence,
but are not fresh CI gates. Several still import `terracotta_rust` or assume
old binary/artifact paths; the current installed product is `rcfp`.

Next implementation action: create the case-to-contract manifest and portable
installed-wheel standalone harness from steps 1–2 of the plan, then add a
fail-closed correctness dependency to the release workflow before another
public tag. Preserve the existing packaging changes and P08 evidence. Re-run
existing validators only after migrating their paths/names and separating the
oracle environment from the installed candidate. Keep real AWS and external
basemap claims open until separately qualified or explicitly scoped out.

Validation for this documentation task: inspect the changed files and run
`git diff --check` on tracked changes; no runtime checks or hosted CI runs.
