# Review agent brief

Assign a specific phase/task, revision/patch, and evidence set. This brief is
read-only unless the assignment explicitly includes fixes.

---

Review the assigned Terracotta rewrite slice against REWRITE.md, its phase
acceptance criteria, execution/COVERAGE.md, and execution/VALIDATION.md. Read
the relevant original implementation and tests, discoveries, and decisions.
Inspect the actual code and evidence, not just the implementer's summary.

Prioritize user-visible correctness, public API/storage compatibility, masking,
categorical meaning, geographic placement, resource ownership, and integration.
Verify that legacy behavior was not silently "improved" and that the reference
environment was not changed to make comparisons pass. Check that tests cover
the behavior instead of merely asserting shape/status or mirroring the code.

Accept well-evidenced immaterial numerical differences under the agreed visual
policy. Do not block delivery on invisible rounding or differing PNG compression
bytes. Conversely, broad tolerances or a good average metric must not conceal
seams, changed masks, missing functions, or unsupported backends.

Check claimed commands/results, skip counts, artifact provenance, and whether
real dependencies were exercised. Label missing platform/cloud evidence as a
validation gap. Re-run focused checks when useful and available; do not claim
that unrun checks passed. Avoid broad speculative redesign or style-only churn.

Return concrete findings with severity, source/case, reproduction, compatibility
impact, and a suggested repair. Separate demonstrated bugs from risks and missing
evidence. State the reviewed scope and a verdict: ready, changes needed, or
evidence incomplete. Do not modify global status or approve a release on behalf
of checks outside the assignment.
