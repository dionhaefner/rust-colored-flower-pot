# Working on RCFP

Read [design/README.md](design/README.md) for maintainer context. Consult
[design/decisions.md](design/decisions.md) before architecture changes and
[design/compatibility-notes.md](design/compatibility-notes.md) when changing
rendering, metadata, catalog access, or Python/HTTP contracts. Current validation
limits and pending qualification are in [design/human-validation.md](design/human-validation.md).

Keep design rationale and agent context in `design/`, user/operator documentation
in `docs/`, supported build/release helpers in `packaging/`, and maintained tests
and reference acquisition in `tests/`. Keep temporary plans, experiments, acquired
references, large fixtures, and generated reports in ignored `build/`, `.cache/`,
or temporary storage. Do not recreate a phase execution harness.

Keep development history and internal task identifiers out of CLI output, HTTP
responses, Python APIs, package metadata, embedded assets, logs, source comments,
and tests. Before finishing, scan the product tree for stale phase labels,
reference workspace paths, and obsolete package identities. Intentional reference
test paths and package-exclusion assertions are supported test behavior.

Hatch VCS versioning is part of the supported package build. Preserve it in wheels
and source archives. Keep developer context out of distributable artifacts and
container build contexts.

Preserve existing user changes. Complete authorized work end to end and run
checks appropriate to the changed behavior. Use real, checksum-pinned raster
fixtures where possible; document narrowly justified synthetic edge inputs.
Never regenerate reference golden outputs from RCFP results. Record exclusions,
skips, and unavailable checks separately from passes; prepared CI is not execution
evidence. Session authorization governs publishing, deployment, and cloud spending.
