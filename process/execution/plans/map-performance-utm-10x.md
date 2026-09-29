# Native UTM COG map-navigation fast path

## Objective

Reach and validate about 10× faster newly visible tile-pan completion than the frozen Python service on the **original, unmodified EPSG:32632 Sentinel-2 COGs**, singleband and RGB, at 1/4/8 users. Keep equal inputs and 160 MiB aggregate cache, exact decoded images where practical, no failed requests, and retain first-view/tail/CPU/RSS limits.

## Work

1. Profile the current UTM general GDAL path and try existing bounded tile prefetch on the original COGs to establish its contribution.
2. Reproduce GDAL's two-stage geometry and image/mask semantics for a narrow UTM/UInt16 interior fast path. Use measured trials to decide which GDAL tasks to replace and which to retain; preserve general fallback.
3. Compare complete viewport responses against frozen Python and inspect any mismatched pixels/masks at ordinary scale and tile seams before accepting a deviation.
4. Tune without unequal source preparation, CPU affinity or aggregate cache. Repeat the full 1/4/8-user singleband/RGB matrix over three isolated trials and retain artifacts.
5. Update evidence, discovery/decision logs, coverage/status and a resumable handoff. Preserve the earlier Web Mercator qualification separately.

## Acceptance

A 10× claim requires three-trial median paired pan speedups at least 10× in every singleband/RGB 1/4/8-user cell, no HTTP failures, at least 95% opaque real tiles, and exact or specifically explained visually immaterial decoded differences. Report each trial, cold views, p95, CPU and RSS. If the stricter per-trial threshold misses, state it.

## Completion (2026-09-28)

The scoped original-UTM target is complete for zoom-13 near-native, opaque Sentinel-2 interiors. All 18 paired pan p50 ratios across three trials exceed 10×, with minimum 13.24×; all 4,992 HTTP requests succeed. The original strict hash comparator records the explained RW-046 image difference, while all 412 alpha masks and the narrow visual-compatibility gate pass after ordinary-scale review. Zoom-12/14 and sampled partial-mask source edges remain byte exact through general GDAL. [Evidence](../../validation/evidence/p08/map10-utm/README.md), [decision](../DECISIONS.md), [handoff](../handoffs/map-performance-utm-10x.md).
