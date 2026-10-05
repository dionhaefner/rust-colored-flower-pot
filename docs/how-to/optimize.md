# Optimize rasters for tile serving

Create cloud-friendly copies when full-strip TIFF reads or missing overviews
make your service slow. Keep your original data until the outputs have been
validated. Optimization is optional for catalog/server replacement; Terracotta
migration does not require raster conversion.

```sh
rcfp optimize-rasters 'docs/examples/data/red.tif' -o optimized --compression deflate
```

The output is `optimized/red.tif`. Register that output in a new catalog, check
its metadata and representative previews/tiles, and compare source values and
masks where the preparation keeps the original grid. The command uses the first
band of each input; split multiband sources deliberately before treating them as
independent catalog datasets.

Use `--resampling-method nearest` for categorical data. `--reproject` creates a
new projected grid and may change sampling; it is a data-preparation choice,
not a condition of using RCFP. `--overwrite` replaces existing outputs;
`--skip-existing` preserves them. Those flags are mutually exclusive. Native
`--nproc` controls preparation parallelism separately from serving workers.
Check the [CLI reference](../reference/cli.md) for profile-specific flags and
[COGs and rendering](../explanation/rendering.md) for why overviews matter.

## Prepare a map-aligned serving grid

In native mode, prepare a Web Mercator COG aligned to the standard map tile grid:

```sh
rcfp optimize-rasters '/data/*.tif' -o map-grid --web-mercator-grid --block-size 1024 --compression deflate
```

Enable the native direct reader (the default) when serving these derivatives.
Use `--resampling-method nearest` for categories. Blocks may be 256, 512, or
1024 pixels. Larger blocks can reduce remote range round trips; smaller blocks
can reduce overfetch. Measure both viewport latency and source bytes for your
zoom range. The grid uses four aligned overview levels and the COG driver's
map-compatible resolution selection. It changes sampling and can add padding;
verify geographic placement and invalid coverage after preparation. Float rasters
without nodata/alpha use NaN as padding nodata, preserving valid zero elevations.

`--all-bands` retains every source band in this map-grid mode. Ordinary preparation
continues to select band 1. Grid preparation is native-only and cannot be combined
with `--reproject`. Custom block size and `--all-bands` require the grid option.
The HTTP tile endpoints can select source bands as described in the
[HTTP reference](../reference/http.md). Metadata still describes band 1: provide
explicit numeric stretches for other bands.

A pixel-interleaved RGB asset can share one dataset/warp across channels, but
singleband users may download and decode unused channels. Keep separate bands
for mixed workloads unless your measurements justify bundling. Record build
time, derivative size, and the cost of refreshing them when originals change.
