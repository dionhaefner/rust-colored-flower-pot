# Map navigation 10× investigation

This addendum qualifies a Rust-specific, opt-in path for **newly visible singleband and RGB map tiles**. Both servers read the same three derived Web Mercator Sentinel-2 COGs. The Python reference received exactly the same preparation; preparation is not counted as a speedup. The frozen Python source is commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`. The qualified Rust executable is SHA-256 `0b27378a546962426e0e05b85136a5d3d129f0d2baa48616fb04274fd70d8ffe`.

The measured unit is completion of all new tile responses for a 1024×768 viewport. A session first requests 12 tiles, then pans horizontally four times (three new tiles each) and vertically twice (four new tiles each), with 100 ms between completed views. There are six requests in flight per session. Three isolated repetitions alternate server order; each server starts fresh. Three CPU cores, three Gunicorn sync processes versus three Rust raster workers, 96 MiB aggregate GDAL cache, and 64 MiB aggregate application/source caches per engine. Rust divides its 64 MiB into 48 MiB raw raster cache and 16 MiB decoded source-block cache. No HTTP failures, retries, or excluded images. The [full report](report.json) retains all 4,992 request records, decoded-image hashes, commands, CPU, RSS and cache metrics. [Summary](summary.json) retains paired trial ratios and source hashes.

| Users | Image | Python pan p50 ms | Rust pan p50 ms | Median paired speedup | Python / Rust pan p95 ms | Python / Rust first-view p50 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | singleband | 38.1 | 2.9 | 11.84× | 55.0 / 4.1 | 199 / 55 |
| 1 | RGB | 84.0 | 7.3 | 11.56× | 135.3 / 8.4 | 384 / 91 |
| 4 | singleband | 46.2 | 3.1 | 15.86× | 95.4 / 6.3 | 416 / 97 |
| 4 | RGB | 223.8 | 18.6 | 12.17× | 357.5 / 28.7 | 1108 / 234 |
| 8 | singleband | 198.3 | 5.7 | 34.19× | 333.8 / 10.9 | 991 / 155 |
| 8 | RGB | 743.8 | 27.2 | 27.32× | 1210 / 46.1 | 2531 / 425 |

Every one of 412 distinct URLs produced exactly the same decoded RGBA image in Rust and Python; every measured tile was at least 95% opaque. All six workload medians clear 10×. One of the 18 paired trial cells, single-user RGB in trial 1, measured 9.62×; its other trials measured 14.75× and 11.56×. Thus the result is a three-trial **median** 10× qualification, not a guarantee that every short run exceeds 10×. First views improve about 3.6–6.0× and do not meet the 10× pan claim. The workload measures loopback server delivery with a warm OS disk cache; it does not measure WAN transfer, continuous drag, browser paint, or remote S3. A single session contributes only six pan views per trial, so its p95 is a sparse tail estimate. At eight RGB users, median process CPU time was 24.74 s for Python and 4.04 s for Rust; the largest observed summed RSS was 450 MiB and 415 MiB respectively. RSS sums Python worker pages and is not proportional set size.

## What changed in Rust

- Worker-owned GDAL source handles and cached suggested warp geometry avoid repeated file open and warp setup on the general path.
- The qualified Web Mercator path uses GDAL for COG block decoding/I/O and the final average read from a MEM raster. Rust performs affine source-window selection and the padded bilinear sampling stage. It accepts only UInt16, north-up, same-CRS, fully valid interior tiles with bilinear reprojection and average resampling. All other cases fall back to the existing GDAL path.
- A bounded, application-owned decoded-block cache is shared across Rust raster workers. A separate bounded raw-raster cache precomputes cardinal neighbors while workers are idle. A cache hit schedules its own neighbors, and HTTP delivery wakes the worker **after** sending the response. Foreground queued work takes priority. This matters for one-tile pans because the next exposed column or row is usually already decoded and sampled.
- Compact PNG encoding remains the default. An exploratory unfiltered RGB PNG was faster locally but about 27% larger than the qualified Rust PNG, so it was removed. One-stage GDAL RasterIO sampling and a zoom-wide shared VRT changed decoded pixels and were discarded. A source-block-only prefetch path improved load tests but did not clear single-user RGB; running both prefetchers contended for CPU.

The [original unmodified UTM Sentinel COG fallback report](original-fallback-report.json) covers the same URL tracks and exact decoded images with the fast-path flag present. Its one-user p50 is 2.79× singleband and 2.14× RGB; the Web Mercator speedup does **not** apply to arbitrary projections. Reaching similar speed on UTM would require a fast coordinate-transform/warp path, correct overview selection, masks and nodata, and parity across edges and data types. GDAL remains the COG/TIFF decoder and general fallback.

## Data and reproduction

The [source manifest](source-manifest.json) records SHA-256 hashes for all original and derived bands. The real Sentinel-2 bands were reprojected with bilinear sampling to EPSG:3857, then copied as tiled DEFLATE COGs with 2/4/8/16 overviews. The three resulting COGs pass the frozen `terracotta.cog.validate`; the red-band recipe was independently rerun and produced the exact benchmark SHA-256. Run alone on a host with the pinned reference environment and corpus:

```sh
.venv-reference/bin/python validation/tools/map10_mercator.py
validation/tools/cargo.sh build --locked --offline --release
.venv-reference/bin/python validation/tools/p08_navigation.py \
  --rust-binary target/release/terracotta \
  --out validation/artifacts/NEW-map10 --repetitions 3 --users 1 4 8 \
  --source-dir .cache/map10-mercator-cog \
  --rust-gdal-mib 96 --rust-app-mib 48 --rust-source-cache-mib 16 \
  --python-gdal-mib-per-worker 32 --python-app-mib 64 \
  --native-env TC_RUST_MERCATOR_FAST=1 \
  --native-env TC_PREFETCH_TILE_RING=1
```

The P08 release archives and native wheel predate this addendum. This source build was qualified locally on Linux x86_64; packaging, other platforms and cloud services have not been requalified for this performance change.
