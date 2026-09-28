# Original UTM COG map-navigation qualification

This follows [the exact affine Web Mercator experiment](../map10/README.md) and uses the **original, unmodified** Sentinel-2 red/green/blue COGs at `.cache/corpus/s2-{red,green,blue}.tif`. All three are 10,980² UInt16 EPSG:32632 rasters with 2/4/8/16 overviews. Python and Rust read the same checksummed files in each paired trial; the Rust rewrite, not source preparation, supplies the new path.

## Measured result

The workload delivers newly visible zoom-13 256² tiles for a 1024×768 view, then six pans. A session keeps downloaded tiles, pauses 100 ms between views, and issues up to six requests concurrently. Runs use three pinned CPU cores, 3 Python Gunicorn workers versus 3 Rust raster workers, warm OS disk cache, fresh services, no retries, and 160 MiB total logical cache on each side. Python uses 3×32 MiB GDAL plus 64 MiB application cache. Rust uses 16 MiB GDAL plus 32 MiB application cache plus 112 MiB shared decoded-source cache. Engine order alternates across three trials. The [raw report](report.json) contains all 36 runs, 4,992 successful requests, 412 distinct URLs, HTTP/resource counters, SHA-256 inputs and the exact native binary hash; the [qualified summary](summary.json) retains every paired speedup. Neither browser paint nor WAN latency is included.

| Sessions | Mode | Python pan p50 | Rust pan p50 | Paired median gain | Weakest trial gain | First-view p50 Python → Rust | Pan p95 Python → Rust |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | Singleband | 68.39 ms | 2.77 ms | 24.66× | 24.46× | 302 → 99 ms | 97 → 4 ms |
| 1 | RGB | 180.96 ms | 5.85 ms | 30.94× | 21.36× | 688 → 164 ms | 262 → 12 ms |
| 4 | Singleband | 113.93 ms | 4.76 ms | 23.94× | 16.83× | 750 → 196 ms | 174 → 16 ms |
| 4 | RGB | 528.19 ms | 32.83 ms | 16.09× | 15.46× | 2000 → 443 ms | 689 → 51 ms |
| 8 | Singleband | 368.56 ms | 11.02 ms | 33.44× | 32.99× | 1522 → 333 ms | 564 → 23 ms |
| 8 | RGB | 1258.37 ms | 91.40 ms | 13.77× | 13.24× | 4335 → 828 ms | 1789 → 242 ms |

All 18 paired pan p50 trial ratios exceed 10×. First views remain 3–5× rather than 10×; this claim is for browsing/panning after the initial view. At eight users/RGB, median peak RSS is about 495 MiB Rust versus 459 MiB Python despite equal configured logical caches. The Rust decoded blocks and GDAL working buffers account for memory outside those limits. Native image values are cached uncompressed; Python uses compressed per-process cache entries. The 8-user/RGB pan p95 ratio is about 7.4×, so a 10× tail-latency claim would be unsupported.

## What contributed to the gain

Stage profiling of the old UTM route put most per-band time in the warped value read (about 11–17 ms), with source opening negligible and VRT setup about 0.9 ms. Retaining GDAL's exact warp but feeding it a cached in-memory source crop still took about 7–9 ms per warm band. The Rust four-pixel sampler reduced the warm transform, interpolation and final-average stages to roughly 0.8, 1.5 and 0.6 ms per band in the exploratory profile. The shared decoded-block cache matters especially under load: with only 16 MiB of it, an exploratory eight-user RGB pan was 248 ms; moving to 80 MiB within the same total budget gave 165 ms, and the final 112 MiB/32 MiB application/16 MiB GDAL split gave a 91 ms median over three trials. These are diagnostic comparisons, not isolated causal estimates, because cache splits and trial timing vary. Idle tile-neighbor prefetch is useful for one-user pans; the shared source cache keeps concurrent RGB work from repeatedly decoding overlapping COG blocks. The frozen Python server had the same source imagery and aggregate logical cache budget, so input preparation explains none of this measured gain.

## Image compatibility and scope

The original strict decoded-hash check reports `passed=false` and 412 mismatches; this is retained plainly in the raw report. The new Rust path uses GDAL's CRS transformation and COG block decoding, shares decoded blocks across workers, then performs fixed four-sample bilinear interpolation in Rust before GDAL's final average downsampling. GDAL's warp kernel widens its bilinear footprint based on the source-window scale of each rotated chunk. This makes the reference slightly smoother in some high-frequency regions. See [GDAL 3.9.3 warp kernel source](https://github.com/OSGeo/gdal/blob/v3.9.3/alg/gdalwarpkernel.cpp), especially `PerformWarp`'s scale selection and the bilinear sample kernel; D-013 and RW-046 record the decision.

The [corpus-specific qualification rule](../../../tools/p08_utm_qualify.py) requires all 412 alpha masks to match exactly, at least 95% real valid coverage for each benchmark tile, per-image RGB mean absolute difference ≤0.61/255, p99 channel difference ≤4/255, maximum channel difference ≤68/255, and every paired pan ratio ≥10×. All hold. The largest channel difference is one pixel in the worst RGB tile; only 2/65,536 pixels in that tile exceed 16/255. I inspected that RGB tile [Python](0-python.png)/[Rust](0-rust.png), the highest mean-difference singleband tile [Python](1-python.png)/[Rust](1-rust.png), and adjacent [Python](python-2x2.png)/[Rust](rust-2x2.png) RGB mosaics at ordinary display scale. No visible change in imagery or tile seams appeared. The mosaic alpha masks match exactly; mean RGB boundary jumps differ by 0.18/255 vertically and 0.31/255 horizontally. The original PNG mosaics are retained without JPEG recompression.

The opt-in fast path (`TC_RUST_UTM_FAST=1`) is intentionally limited to EPSG:32632, UInt16, north-up source, EPSG:3857 target, zoom-13-like near-native resolution, 256² tiles, bilinear warp plus average read, and fully valid padded source windows. GDAL remains responsible for overview selection, nodata/mask and source-edge behavior outside that interior. With the flag enabled, sampled zoom-12 overview and zoom-14 magnification tiles matched Python **exactly**; sampled partial/transparent source-edge tiles also matched exactly (coverage 0%, 3.3%, and 28.6%). The six exact singleband/RGB cases are retained in [fallback.json](fallback.json). Other CRS, source types, resamplers, remote I/O, and broader zoom ranges have no 10× qualification. Cache budgets, CPU count, prefetch usefulness and loaded p95 must be remeasured for production conditions.

## Reproduce

```sh
validation/tools/cargo.sh build --locked --offline --release
PYTHONPATH=.cache/reference .venv-reference/bin/python validation/tools/p08_navigation.py \
  --rust-binary target/release/terracotta --out validation/artifacts/map10-utm-recheck \
  --repetitions 3 --users 1 4 8 --source-dir .cache/corpus \
  --rust-gdal-mib 16 --rust-app-mib 32 --rust-source-cache-mib 112 \
  --python-gdal-mib-per-worker 32 --python-app-mib 64 \
  --native-env TC_RUST_UTM_FAST=1 --native-env TC_PREFETCH_TILE_RING=1 \
  --compare-pixels --allow-mismatch
.venv-reference/bin/python validation/tools/p08_utm_qualify.py \
  validation/artifacts/map10-utm-recheck/report.json \
  --out validation/artifacts/map10-utm-recheck/summary.json
PYTHONPATH=validation/tools .venv-reference/bin/python validation/tools/p08_utm_edges.py \
  --database validation/artifacts/map10-utm-recheck/catalog.sqlite \
  --rust-binary target/release/terracotta \
  --out validation/artifacts/map10-utm-recheck-edges
```

`--allow-mismatch` keeps exact-hash failures visible for the later, narrow image-compatibility check; it never marks the strict run as passed. Clippy with `-D warnings`, fmt and 20 Rust unit tests pass on the final source. The final binary SHA-256 is in [summary.json](summary.json). Release archives and nonlocal platform builds predate this optimization.
