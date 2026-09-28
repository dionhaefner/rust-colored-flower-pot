# Handoff: original UTM COG map-navigation 10×

Date: 2026-09-28. Status: local scoped performance and visual-compatibility qualification complete; released artifacts unchanged.

The final source adds [the narrow UTM reader](../../src/raster/utm.rs) ahead of the exact affine Web Mercator path and general GDAL fallback. Set `TC_RUST_UTM_FAST=1` to enable it. It accepts only fully valid padded UInt16 EPSG:32632 source interiors, EPSG:3857 zoom-13-like near-native 256² tiles, bilinear warp and average final read. GDAL performs CRS coordinate transformation, COG decoding and final average resampling. Rust shares decoded source blocks across workers and interpolates four source pixels. `TC_PREFETCH_TILE_RING=1` enables idle cardinal-neighbor raw-raster precomputation. At overview/magnification scales and source mask/nodata/edges, the general GDAL path is exact.

[Final evidence and reproduction](../../validation/evidence/p08/map10-utm/README.md), [all paired runs and pixel comparisons](../../validation/evidence/p08/map10-utm/report.json), [qualification summary](../../validation/evidence/p08/map10-utm/summary.json). The native binary hash is `f7843c2fffb16db78c13bac677870d578eca83a040be81ad5276487fdc83effc`. Equal-input/equal-160-MiB/three-core trials pass all 18 pan p50 pairs above 10×; weakest is 13.24×, and 8-user RGB median is 13.77×. First views and 8-user RGB p95 are below 10×.

The strict decoded-image hash result is **failed** (412/412 distinct URLs differ); do not label it exact. RW-046 and D-013 accept this single operation's small, explained visual difference after an ordinary-scale worst-case and seam review. All alpha masks match, every image p99 RGB channel difference is ≤4/255, and the worst tile has only two pixels above 16/255. The reference and Rust worst-case pairs and 2×2 mosaics are retained with the evidence. The corpus-specific gate is [p08_utm_qualify.py](../../validation/tools/p08_utm_qualify.py). Sampled zoom-12/14 and partial/transparent footprint edges remain exact under the same flag.

Failed/alternate diagnostics are ignored under `validation/artifacts/map10-utm-*`: the original general path with tile prefetch alone, a direct manual sampler with 16 MiB decoded-block cache, and an exact GDAL warp over an in-memory source crop. The exact crop matched pixels but ran too slowly under loaded RGB. The final allocation is 16 MiB native GDAL, 32 MiB raw raster and 112 MiB shared decoded blocks; Python has three 32 MiB GDAL worker caches and 64 MiB app cache. No imagery was reprojected or prepared uniquely for Rust. No cloud, container or publishing actions were performed.

Checks: release build, fmt, Clippy `--all-targets -- -D warnings`, 20 Rust unit tests, three paired navigation trials, corpus-specific visual gate and exact fallback spot checks. Remaining work before broader claims: support other UTM zones/CRSs and overview-scale fast reads, validate nodata edge behavior beyond fallback, test remote I/O and target platforms, benchmark first views and loaded p95. The pre-existing exact Web Mercator path and its separate [handoff](map-performance-10x.md) remain intact.

## Later local follow-up

[Suggested-geometry cache reuse](map-performance-utm-geometry.md) supersedes the
performance numbers and binary hash in this handoff while leaving its image
compatibility scope unchanged. The previous report remains the direct A/B baseline.
