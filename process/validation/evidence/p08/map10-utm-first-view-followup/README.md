# First-view follow-up: duplicate block loads and transformer reuse

Both experiments used the same original EPSG:32632 Sentinel-2 COGs and the qualified zoom-13 map tracks: 1/4/8 sessions, singleband/RGB, three pinned CPU cores and raster workers, fresh service per cell, and the same 160 MiB aggregate logical cache. Alternating Rust A/B comparisons decode images before comparing them. The unchanged qualified binary is SHA-256 `3fa0b076f977a62a97e6247aa64bb0a64aecbe6356971f26c59a7cc861536ba5`.

## One decoder per shared source block

The [singleflight candidate](singleflight-report.json) made simultaneous misses for the same `path + size/mtime + block coordinates` wait for one GDAL decode. It kept I/O outside the shared cache mutex; failures were shared only with current waiters. Three alternating trials found all 412 distinct RGBA images exact against the old Rust build. It did **not** reliably improve first-view completion: one-user RGB median was 162.23→153.46 ms, with one of three paired trials slower; eight-user RGB was 679.15→692.18 ms, with only one of three paired trials faster. Pans also varied.

A diagnostic run counted concurrent duplicate waits over the **whole** navigation run: one-user singleband 9, one-user RGB **1**, eight-user singleband 22, eight-user RGB 15. This explains why the mechanism has little leverage on the RGB first viewport: its three bands live in separate TIFFs, and most cold blocks are genuinely distinct. The candidate was removed. The diagnostic logs are under ignored `process/validation/artifacts/map10-utm-singleflight-profile/`.

## Reusing a GDAL projection transformer

The bundled GDAL 3.9.3 exports `GDALSetGenImgProjTransformerDstGeoTransform`. A candidate kept one GenImgProj transformer per worker and stamped local source, resetting its destination grid for each tile and creating a fresh approximation wrapper. Remote sources kept per-read transforms. The [three-trial whole-matrix A/B](transform3-report.json) was exact for 412 URLs; some first-view cells improved, but its result was noisy.

The [nine-trial RGB A/B](transform_rgb9-report.json) resolved the first-view question. One-user first-view median was **153.57→156.07 ms** (candidate faster in only 2/9 pairs); eight-user median was **711.95→725.46 ms** (faster in 5/9). Pan medians improved modestly, 7.13→6.52 ms for one user and 56.78→55.63 ms for eight. All 206 distinct RGBA images matched exactly. The final candidate's separate [three-trial whole-matrix A/B](transform3-report.json) also matched 412 URLs exactly. Because the intended first-view improvement did not hold up and the cache adds GDAL handle lifetime complexity, the candidate was removed.

The earlier version of the transformer candidate also passed the full frozen-Python UTM qualification (18 paired pan cells above 10×, existing RW-046 image bounds) and six exact overview/edge fallback cases. These checks did not change the first-view conclusion. The complete [numeric summary](summary.json) retains group medians, paired counts and binary hashes; exploratory logs and Python comparison remain under ignored `process/validation/artifacts/map10-utm-transform-*`.

The release source and binary were restored to the qualified geometry-cache baseline; SHA-256 was rechecked after rebuilding. No new first-view speedup is claimed. The remaining cost is mainly the first projection initialization and decoding unique COG blocks, plus waiting for all 12 viewport tiles. A future effort should target one of those costs directly and include startup time if it prewarms worker state.
