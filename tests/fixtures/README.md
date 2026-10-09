# Public raster fixtures

The [corpus guide](../../docs/explanation/raster-corpus.md) describes coverage,
independent expectations, performance interpretation and fixture contributions.
The [test guide](../README.md) and [reference guide](../reference/README.md) provide
runner commands and acquisition procedures.

`corpus/manifest.json` pins native Sentinel-2, validity-edge, Copernicus DEM and
ESA WorldCover windows. `tile-grids/manifest.json` pins native DEM windows for
Arctic, southern and adjacent dateline coordinates. Both retain unchanged real
samples and their parent provenance. Run `tests/corpus_contracts.py` to verify
file, decoded pixel/mask, grid and numerical identities offline before using
these inputs with another implementation.

`navigation.json`, `geography-sources.json`, and `stac-live.json` describe larger
explicitly acquired sources. Demo manifests pin independently acquired reference
imagery. Follow each maintained runner's acquisition command. Keep downloads,
derived rasters, timing captures and reference reports in ignored storage.
Never regenerate a reference image or expected array from RCFP output.
