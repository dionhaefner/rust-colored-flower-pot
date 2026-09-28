# Map-navigation 10× handoff

Date: 2026-09-27. Scope: user-requested performance investigation, not a P08 release seal.

The opt-in Rust affine Web Mercator path plus bounded idle neighbor precomputation reaches >10× median paired pan completion in all six singleband/RGB, 1/4/8-user workloads. [Evidence, raw samples, limitations and reproduction](../../validation/evidence/p08/map10/README.md). The executable SHA-256 is `0b27378a546962426e0e05b85136a5d3d129f0d2baa48616fb04274fd70d8ffe`. Source hashes for all three equal-input derived COGs are retained. The frozen Python reference was read-only.

Changes: `src/raster.rs` reuses local GDAL handles and suggested geometry; `src/raster/fast.rs` owns exact same-CRS source-window/bilinear sampling and an executor-scoped shared decoded-block cache; `src/execution.rs` precomputes cardinal-neighbor raw rasters at idle priority; `src/http.rs` wakes prefetch after response delivery; `validation/tools/p08_navigation.py` supports explicit candidate, source directory and cache budgets; `validation/tools/map10_mercator.py` derives the equal-input COGs. The general GDAL path remains for UTM and unsupported sources. D-012 and RW-045 explain scope and rejected approaches.

Validation: Rust fmt, Clippy `-D warnings`, 20 unit tests pass. Full paired benchmark: 36 runs, 4,992 requests, no HTTP errors, all 412 unique decoded images exact and at least 95% opaque. Minimum paired-trial speedup is 9.62× for single-user RGB; median is 11.56×. Original UTM fallback: 6 paired runs, 1,664 requests, exact images, one-user 2.79× singleband and 2.14× RGB. Derived COGs pass frozen Terracotta COG validation; the red-band recipe reproduces its SHA-256 exactly. Reproduction commands are in the evidence README.

The P08 release archives and native wheel predate this code. Their earlier qualification remains historical evidence, not qualification of the new source. External platform, AWS and basemap gates remain in STATUS.md. Further broadening should first target accurate same-output transforms/overviews/masks for UTM and edge tiles, then measure remote delivery and packaged platforms. No publish, deployment or cloud operation was performed.
