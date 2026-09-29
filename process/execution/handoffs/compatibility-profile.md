# Terracotta compatibility profile design handoff

The [design](../plans/compatibility-profile.md) and [D-023](../DECISIONS.md)
record the proposed direction. No product feature, migration guide, or CI gate was
implemented in this design task. The current `rcfp` wheel must not be described
as a Python/CLI drop-in replacement.

The next implementation slice is profile resolution and immutable propagation
through Python, CLI, native sessions, and workers, plus a legacy-format write
fence. Then fill the public Python and CLI inventory against the pinned
Terracotta 0.10.1 sdist, add installed-wheel differential cases for both
profiles, and publish the two end-user guides before enabling a drop-in claim.

Design checks completed: repository surfaces, existing differential CI,
storage migration behavior, and package identity were inspected; new process
links resolve; `git diff --check` passes; the required scan outside `process/`
found no stale phase names, reference workspace paths, or obsolete package
identities in tracked text. Hosted CI and runtime behavior are pending by
design. Existing unrelated working-tree changes were left in place.
