# Validation discipline

The compatibility standard is in [REWRITE.md](../REWRITE.md). Validate observable
contracts and real application appearance; do not pursue byte-identical PNGs or
invisible floating-point artifacts. This document defines evidence, not a blanket
numerical threshold or a requirement to run every test after every small change.

## Establish an honest reference

P00 must supply actual runner commands and record:

- Full reference source commit, snapshot provenance, and any local modifications.
- Python, packages, GDAL/PROJ/geometry/codec versions and data files; Rust
  toolchain, dependencies and native libraries; OS and architecture.
- Pinned real source assets, acquisition metadata/checksums, exact subset recipes,
  CRS, dtype, masks, overview/compression settings, and expected test purpose.
  Record modifications and any justified synthetic microfixture separately.
- Runtime settings and relevant environment values, with credentials redacted.
- Requests/operations and capture format; run IDs, commands, exit codes, results,
  and artifact checksums/locations.

Use an isolated snapshot/environment; do not repair the oracle by editing the
user's Python checkout. If the source fails with current dependencies, first
reconstruct a suitable pinned environment. Distinguish installation failures,
existing failing tests, and behavior gaps. Any unavoidable oracle patch must be
retained, explained, and excluded from claims of unchanged-source equivalence.

Never regenerate golden outputs from Rust and call them reference outputs.
Baseline changes need an explicit reason and old/new evidence. Do not casually
upgrade GDAL or change projection data in the middle of a parity investigation.

## Build the executable harness in P00

Follow [DATASETS.md](DATASETS.md): download a compact real-world corpus in P00,
prefer recognizable native-resolution crops, and retain a separate large-file
tier. Synthetic data is a narrowly justified supplement, not the visual baseline.

Expose documented commands for asset acquisition/subsetting, Python capture, Rust capture
(when available), comparison, and focused/full runs. Permit a configurable
reference path. The runner should produce machine-readable results and human
inspection artifacts, fail on material mismatches, and report skips separately.

Prove the comparator using controlled mutations: changed route status, missing
field/key, wrong dimensions, altered category/mask, shifted image, and visible
seam. Self-comparison alone can pass even with a broken comparator. Test any
normalization so it cannot erase meaningful differences such as list/key order
that determines dataset identity or rendering.

Keep provenance/manifests/recipes and suitable small fixtures versioned, respecting
the source's redistribution terms. Cache acquired assets for offline routine tests;
network acquisition must be explicit, reproducible, and separate from test execution.
Keep large inputs outside Git. Keep large outputs
and benchmarks in ignored artifact storage with retention instructions. A local
temporary path alone is insufficient evidence for another agent; retain a small
reproduction or a durable artifact reference before marking a contract verified.

## Compare the right things

| Contract | Comparison |
| --- | --- |
| HTTP | Route/method, query parsing/defaults, status, content type, meaningful headers/CORS, JSON structure/types/ordering where contractual, error meaning and version negotiation. Normalize only proven irrelevant fields. |
| Raster/PNG | Dimensions, decoded color/transparency, masks and classes, bounds/geographic placement, seams, and visible changes at ordinary display scales. PNG compressed bytes can differ. |
| Compute/color | Parser semantics, operations, invalid results, mask propagation, categories, channel ordering; compare intermediate arrays to diagnose failures, without imposing unnecessary bit equality. |
| Metadata | Ranges, precision/storage round trips, statistic definitions, finite/invalid behavior, geometry and percentile effects. Use separate checks for full, sampled, and chunked modes. |
| Database | Legacy encodings, ordered keys, paths, precision, transactions, updates/deletes, migrations, and Python↔Rust reads/writes on real supported engines. |
| CLI/Python | Real invocation/import, flags/defaults, configuration precedence, exit/error behavior, documented types and contexts/futures, and complete ingestion/serving flows. |
| Deployment | Clean artifact installation/loading, native libraries/data, request/response path, platform/runtime, credentials and remote I/O. Emulator and event-unit evidence do not prove a deployed service. |

## Handle numerical and visual differences

Start with exact comparisons where useful for diagnosis; escalate only meaningful
differences. Inspect ordinary-scale side-by-side views for the affected behavior.
Zoomed/contrast-amplified differences locate causes but do not define acceptance.
Check localized masks/seams and categorical values independently of global metrics.

For a small explained difference, retain the reference and Rust sample, cause,
viewing context, effect on downstream semantics, and a narrow regression rule.
Record an accepted-deviation RW entry and move on. Avoid acceptance rules such as
"all pixels may differ by N" without evidence for the specific operation and
data classes. Classification/mask/geographic errors are not rounding artifacts.

If ordinary-scale visual inspection is unavailable, report that gap honestly.
Numerical evidence can support a hypothesis; it cannot be described as a visual
inspection that never happened. Seek the missing check only when necessary for
the claim, and continue other work.

## Layer checks and preserve failures

1. Run targeted unit/contract checks during implementation, including the case
   that motivated the change.
2. Run the slice through its real adapter, storage, and native raster path.
3. Run relevant existing tests and cross-language comparisons.
4. At a phase exit, run its declared integration/packaging/resource checks.
5. At P08, qualify the combined build across the declared support matrix.

Use actual project commands once they exist; the plan is not evidence of a pass.
Record passed, failed, skipped, and not-run checks separately. Include counts and
reasons for skips when suites can silently omit a backend or optional dependency.
Mocks are useful at boundaries but do not clear the corresponding integration row.

Known legacy bugs should be reproduced and logged. A new Rust failure is a port
regression to fix now, not a legacy issue to defer. Do not weaken unrelated tests
or swallow errors to make results green. Keep the smallest failing reproduction.

## Performance and resource evidence

Compare equivalent features, data, cache conditions, native-library builds,
CPU/memory limits and concurrency. Use a production Python serving setup, not
the serial development server, and disclose any dependency differences.

Measure local/remote, cold/warm, singleband/RGB/compute/preview and representative
zoom/projection cases. Report latency distributions and repetitions, throughput,
peak memory, handles, remote requests/bytes, startup and artifact size. Include
measurement uncertainty and setup. A faster isolated pixel loop does not prove
a faster complete tile service; a warm run does not prove cold-start behavior.

Choose workload-specific resource budgets from measurements and document them.
Validate bounded execution under overload and failure, cache identity/invalidation,
and behavior when clients disconnect. Keep service tests disposable; avoid real
cloud costs without authorization. Local substitutes leave real-cloud checks open.
