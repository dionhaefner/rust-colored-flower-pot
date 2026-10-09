# Shared real-raster corpus

The public corpus makes source geography, masks, categories and numerical values
reusable across RCFP and independent implementations. Small native pixel windows
are kept in `tests/fixtures/corpus/` and `tests/fixtures/tile-grids/`. Manifests
record source URLs, parent/file checksums, crop recipes, attribution, CRS and
affines. They contain Sentinel-2 reflectance, a source validity edge, Copernicus
DEM elevations, ESA WorldCover classes, and Arctic/southern/dateline windows.
The southern grid fixture is near 34°S, not Antarctica.

## Integrity and independent expectations

Run the offline integrity gate in an environment with NumPy and Rasterio:

```sh
python tests/corpus_contracts.py --out build/corpus-integrity
python -m unittest discover -s tests -p 'test_corpus_contracts.py'
```

The gate checks file and decoded sample/mask hashes, dtype, grid, nodata,
geographic bounds, center values and valid counts. It records fixture and checker
identities plus the independent reader's versions. Corruption controls prove
that changed pixels, masks, categories or placement cannot be accepted
by updating the file checksum. The gate itself does not run an RCFP render.

Reference acquisition and comparisons live in `tests/reference/`. Terracotta
0.10.1 is acquired from a pinned release archive and isolated dependency lock.
GDAL/Rasterio and NumPy provide independent native grid/value expectations.
Large originals, derivatives, oracle captures and generated reports stay in
ignored storage. Candidate output is never a reference golden.

| Consequential regression                                                                            | Maintained comparison                                                                                                                                          |
| --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Geography, rotation, polar/dateline edges, metadata, masks, dtype and all supported warp/read pairs | `tests/reference/raster_boundaries.py` compares both profiles with the pinned oracle                                                                           |
| Native tile placement, overviews, coverage, class colors and supported resampling                   | `tests/native_rendering.py` checks an independent GDAL grid                                                                                                    |
| Calibrated physical bands, expressions, numerical points and named grids                            | `tests/catalog_contracts.py` and `tests/native_services.py` use independent Rasterio/NumPy values                                                              |
| Mosaic ownership, seams, categories and bounded selection                                           | `tests/mosaic_contracts.py` checks independent samples and measured reader/traffic budgets                                                                     |
| HTTP discovery/images, Terracotta migration and writes                                              | `tests/reference/differential.py` and `docs/tools/verify.py` use the pinned Terracotta implementation                                                          |
| Cross-implementation navigation delivery, resources and source traffic                              | `tests/service_benchmark.py` compares pinned RCFP/Terracotta/TiTiler environments. `tests/service_benchmark_quality.py` checks independent saved-tile fidelity |

Detailed commands and prerequisites are maintained in the repository's
[test guide](https://github.com/dionhaefner/rust-colored-flower-pot/blob/main/tests/README.md)
and [reference guide](https://github.com/dionhaefner/rust-colored-flower-pot/blob/main/tests/reference/README.md).
The correctness workflow runs integrity and corruption checks before its raster
comparisons. Configured workflows and earlier local results do not qualify a
new artifact. Report failed/skipped/unavailable cases separately, including the
input-specific bounds for deliberately different native sampling policies.

## Performance interpretation

Use a retained request workload and exact artifact/input hashes. Compare matched
CPU/cache budgets, startup, successful viewport completion, tails, bytes and
memory. Independent image acceptance must precede timing claims. Sampling policies
can differ across implementations. Report those differences separately instead
of classifying a timing result as pixel equivalence.

There is no universal latency or speedup threshold. Loopback, warm storage, real
WAN and physically cold storage measure different conditions. Existing runners
enforce fidelity, bounded work and accounting checks and retain timing data for
comparison. An application's regression budget needs its own repeated baseline,
noise estimate and measured workload. See [performance methods](performance.md)
and [application evaluation](../how-to/evaluate.md).

## Extend the corpus

Submit a small legally redistributable native window or an acquisition recipe for
a checksum-pinned real source. Include parent and derivative hashes, URL/license/
attribution, exact crop/reprojection/overview recipe, band/calibration/nodata/mask
details, and the application failure it reproduces. Establish expected samples,
coverage, category labels, seams and placement independently of RCFP. Retain a
controlled failure proving the assertion detects the original problem.

Use synthetic input only when a real source cannot reasonably exercise a narrow
edge such as nonfinite declarations, malformed directories or fractional alpha.
State that reason beside the recipe and test. Repeated dates or placed copies of
real pixels exercise indexing/selection. They do not establish acquisition history
or production adoption. Add a regression check for each new fixture.
