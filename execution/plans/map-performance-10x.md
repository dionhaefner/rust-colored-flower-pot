# Map-navigation performance investigation

## Objective

Measure and validate an approximately 10x advantage over the frozen Python
Terracotta service for newly requested singleband and RGB map tiles at one user
and under concurrent load. Compare equivalent source imagery, rendering, CPU,
memory, GDAL cache, and application cache budgets. Prepared imagery is not an
RCFP-specific advantage and cannot substantiate the claim.

## Work

1. Restore the pinned Rust/Python runtimes and original COG corpus in ignored
   workspace paths. Preserve the frozen Python reference unchanged.
2. Reproduce the existing map-navigation baseline and add per-stage timing for
   source open, warp setup, value and mask reads, color work, PNG encoding, and
   queueing. Retain CPU, RSS, cache, and decoded-image evidence.
3. Optimize the dominant common tile path, starting with worker-owned source
   handle reuse and removal of redundant warp setup where compatible. Keep a
   general GDAL path for uncommon inputs and preserve visible output.
4. Repeat the 1/4/8-user navigation matrix with fresh services, new-tile pans,
   original Sentinel-2 COGs, matched budgets, and exact decoded-image checks.
   Include singleband and RGB, p50/p95 viewport completion, CPU, and memory.
5. Iterate on measured bottlenecks. Record unsuccessful approaches and all
   compatibility differences. Report a 10x result only if the full qualified
   workload supports it; otherwise record the measured limit and blocker.

## Acceptance

The primary claim requires at least 10x Python/Rust pan-completion improvement
for singleband and RGB with one user and with load, across three isolated trials,
with at least 95% opaque real-data tiles, no failed requests, correct decoded
images, and matched CPU/cache budgets. Cold first views, tails, throughput, and
resource use must also be reported so the result is interpretable.

## Outcome, 2026-09-27

Implemented and validated the opt-in same-CRS affine UInt16 tile path and
bounded neighbor precomputation. The three-trial median 10× objective is met
for all six 1/4/8-user singleband/RGB pan workloads on equal-input Web
Mercator COGs. The stricter per-trial target stated above missed once:
single-user RGB trial 1 was 9.62×. Cold first views and original UTM COGs are
below 10×. Exact image parity and resource evidence are retained in
[map10](../../validation/evidence/p08/map10/README.md). This is a scoped
result, not a claim that the initial port or all GDAL inputs are 10× faster.
