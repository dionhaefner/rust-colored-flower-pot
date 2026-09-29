# Reusing suggested geometry in the original-UTM fast path

The [previous UTM qualification](../map10-utm/README.md) recomputed GDAL's suggested warp geometry for every fast-path tile and band, although each worker already cached that geometry for the general GDAL path. [The small change](../../../../../src/raster.rs) factors out that worker-local cache and lets [the UTM reader](../../../../../src/raster/utm.rs) use it. The source file's size and modification time remain in the worker-owned dataset identity; remote sources still bypass this cache. Raster values, masks, resampling, HTTP responses and the opt-in scope do not change.

## Direct Rust A/B

[Three alternating baseline/candidate trials](ab-report.json) use the same original Sentinel-2 COG catalog, three pinned CPU cores, 3 Rust raster workers, 160 MiB aggregate logical cache (16 MiB GDAL, 32 MiB application, 112 MiB shared decoded blocks), the same 1/4/8-session zoom-13 pan tracks and fresh service per cell. Response receipt is timed before client image hashing. All 412 distinct decoded RGBA images match the previous Rust binary exactly. The [A/B summary](ab-summary.json) retains individual paired ratios.

| Sessions | Mode | Old Rust pan p50 | Geometry-cached p50 | Relative gain |
| ---: | --- | ---: | ---: | ---: |
| 1 | Singleband | 2.89 ms | 2.55 ms | 1.13× |
| 1 | RGB | 6.85 ms | 8.29 ms | 0.83× |
| 4 | Singleband | 4.02 ms | 4.04 ms | 1.00× |
| 4 | RGB | 30.20 ms | 22.70 ms | 1.33× |
| 8 | Singleband | 11.52 ms | 10.16 ms | 1.13× |
| 8 | RGB | 79.57 ms | 52.23 ms | 1.52× |

These are medians of each binary's three trial p50 values; the very small single-user view times have run-to-run noise. The loaded RGB improvement appears in all three paired trials (1.43–1.68× at eight users). Single-user RGB did not improve consistently. This change trades no image fidelity for the loaded gain.

## Frozen Python comparison

The [full three-trial report](report.json) and [narrow image/performance qualification](summary.json) use the same Python reference and cache budgets as the previous UTM result. All 4,992 requests succeeded. All 18 paired pan p50 ratios exceed 10×. Median gains over Python are 24.56×/27.19× (one user singleband/RGB), 29.00×/21.08× (four), and 37.49×/23.09× (eight). Eight-user RGB median pan completion is 1,269.95 ms Python versus 55.01 ms Rust; Rust pan p95 is 181.81 ms and first view 698 ms. The previous final binary's corresponding Rust medians were 91.40 ms pan p50, 241.55 ms pan p95 and 828 ms first view, but the direct A/B above is the stronger attribution. Candidate binary SHA-256: `3fa0b076f977a62a97e6247aa64bb0a64aecbe6356971f26c59a7cc861536ba5`.

The strict decoded-hash result remains `passed=false` against Python: the same 412 small, accepted UTM sampling differences described in RW-046 remain. All 412 alpha masks match exactly. The largest per-image mean absolute RGB channel difference is 0.609/255, per-image p99 ≤4/255 and maximum isolated channel difference 68/255, identical to the prior qualified path. Six sampled overview/magnification/partial-mask fallback cases [remain byte exact](fallback.json). The [earlier ordinary-scale visual pairs and seam mosaics](../map10-utm/README.md) still represent this candidate because the A/B decoded images are identical.

A one-trial probe with **six** raster workers on the same three cores had eight-user RGB pan p50 74 ms and singleband 18 ms; with **two** workers RGB was 108 ms and singleband 8 ms. The three-worker candidate's full three-trial medians were 55 ms RGB and 10 ms singleband. The worker probes do not justify changing the three-worker setting. Their raw exploratory reports are under ignored `process/validation/artifacts/map10-utm-workers{2,6}`.

## Reproduce

```sh
packaging/cargo.sh build --locked --offline --release
PYTHONPATH=process/validation/tools .venv-reference/bin/python process/validation/tools/p08_rust_ab.py \
  --baseline process/validation/artifacts/map10-utm-final/terracotta-baseline \
  --candidate target/release/terracotta \
  --database process/validation/artifacts/map10-utm-final/catalog.sqlite \
  --out process/validation/artifacts/map10-utm-geometry-ab-recheck --repetitions 3
PYTHONPATH=.cache/reference .venv-reference/bin/python process/validation/tools/p08_navigation.py \
  --rust-binary target/release/terracotta --out process/validation/artifacts/map10-utm-geometry-recheck \
  --repetitions 3 --users 1 4 8 --source-dir .cache/corpus \
  --rust-gdal-mib 16 --rust-app-mib 32 --rust-source-cache-mib 112 \
  --python-gdal-mib-per-worker 32 --python-app-mib 64 \
  --native-env TC_RUST_UTM_FAST=1 --native-env TC_PREFETCH_TILE_RING=1 \
  --compare-pixels --allow-mismatch
.venv-reference/bin/python process/validation/tools/p08_utm_qualify.py \
  process/validation/artifacts/map10-utm-geometry-recheck/report.json \
  --out process/validation/artifacts/map10-utm-geometry-recheck/summary.json
```

The baseline binary is retained only under ignored local artifacts. The paired Python report, A/B summary and candidate hash are retained here; reproducing the direct A/B requires that local baseline binary. Rust fmt, strict Clippy and all 20 unit tests pass. Release archives still predate this optimization.
