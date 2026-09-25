# P05 — metadata, ingestion and raster optimization

Completed against frozen Python commit
`6db38d5873cde5ebabe47338a1f61ddc788eada6`, using the P00 pinned environment and
checksum-pinned Sentinel-2, Copernicus DEM and ESA WorldCover corpus. No oracle
source was modified. See [run manifest](run-manifest.json), [plan](../../../execution/plans/P05.md)
and [handoff](../../../execution/handoffs/P05.md).

## Implemented contracts and actual evidence

| Surface | Evidence | Result |
| --- | --- | --- |
| Full/sample/chunk statistics, bounds, hulls, invalid/constant/negative inputs, extra metadata, threshold dispatch | [Preparation report](preparation.json), retained [metadata pairs](metadata/) | Included in 329 passing preparation assertions; mode-specific checks preserve the algorithm and float32 SQLite encoding |
| SQLite create/insert/overwrite/delete, descriptions, rollback, Python↔Rust reads and writes, retained skip-metadata quirks | [Preparation](preparation.json), [packaged workflows](workflows.json), [reference quirks](reference-quirks.json), [Rust tests](rust-tests.log) | Native and Python writes interoperate; failed writes and explicit batches roll back |
| Concurrent lazy first requests and failure/retry | [Workflows](workflows.json) | Eight callers, one audited metadata insert; missing-file failure leaves no metadata; retry succeeds; explicit read-only lazy miss fails |
| Raster patterns, escaped braces, Unicode, repeated adjacent keys, RGB ordering, skip-existing, quiet, real `serve -r` | [Preparation](preparation.json), [workflows](workflows.json) | Real CLI processes, real HTTP metadata/tile/preview requests; native preparation exposed through packaged Python |
| COG optimization, memory/disk paths, reprojection, first-band/dataset-mask semantics, four resamplers, codec/overwrite/skip/quiet/nproc flags | [Preparation](preparation.json), [edge matrix](edges.json) | 140 edge assertions, including actual two-file parallel execution; frozen COG validator passes; base arrays/masks/overview arrays/decoded previews exact |
| Metadata effects on rendering | [Image report](metadata-images.json), [ordinary-size views](metadata-images.png) | 24 full/sample/chunk default and percentile-stretch pairs, 72 status/image assertions, exact decoded RGBA |
| Full 36,000×36,000 WorldCover chunking and disk optimization | [Stress](stress.json) | Actual 1.296 billion source pixels; all output base/mask/overview blocks compared with Python; COG valid; every measured process below 512 MiB |
| Existing rendering/HTTP behavior | [P04 regression](http-regression.json) | 526/526 requests pass |
| Final package Python/WSGI/Lambda-event adapters | [Adapter regression](adapters.json) | 46/46 pass, including concurrent application lifecycle |
| Rust and oracle suites | [Rust](rust-tests.log), [upstream](upstream.log) | 17 Rust tests and 156 unchanged upstream raster/ingest/optimization tests pass; zero skipped; rustfmt and warning-denied Clippy pass |

The upstream 156 tests validate the frozen oracle, not the Rust implementation.
No skipped platform/cloud check is counted as a pass. The final package
preparation and adapter checks postdate all implementation fixes. The full HTTP
regression predates later preparation-only fixes; rendering/HTTP source behavior
was unchanged. The final stress run postdates the GEOS hull repair and predates
only extra-metadata truthiness, pattern backtracking and CLI validation fixes.

## Numerical and visual decisions

RW-024 accepts only full/sample float32 DEM mean/std reduction rounding, bounded
by four float32 ULPs. The largest observed mean delta is 0.0003662109375 (three
ULPs); range and stored percentiles match. Mean/std do not control the reference
stretch. All 24 downstream image pairs are exact. Raw native metadata currently
uses SQLite's float32 percentile boundary; full Python ndarray return fidelity is
part of P07, not a claim made by this preparation adapter.

RW-025 accepts only a 1e-9 relative symmetric-difference-area bound for the
rotated/chunk hull case (observed 2.4215e-10), after repairing a material 0.000151
error with robust GDAL/GEOS hulls and reference affine order. Other geometries
retain 1e-10. [Rotated real-scene hull overlays](rotated-hull.png) were inspected
at 256 pixels per view; boundaries appear identical. The metadata view sheet and
[optimization views](optimization-images.png) and retained PNG pairs were inspected at the same ordinary size.
No mask/category/geographic placement or image tolerance was relaxed.

RW-021 preserves stale metadata on skip-metadata overwrite, per-insert CLI
commits, and the reference's same-length key-name-mismatch behavior. RW-022
preserves Crick's negative-only chunk maximum initialization bug. RW-023 records
repaired optimization window/warp/grid differences. D-007 records native
preparation and lazy transaction ownership; GDAL stays loaded through thread exit.

## Fixture provenance and resource boundaries

Real data come from the existing pinned corpus. `p05_edges.py` makes explicit
512×512 real-Sentinel derivatives for alpha, two-band nodata, an inserted NaN
patch and a 15-degree affine rotation. These isolate semantics; they are not
publisher-native alpha/geographic coverage. The 32×32 constant, negative and
all-NaN DEM derivatives are narrowly justified statistical edge cases.
`p05_stress.py` uses the complete original WorldCover COG, never repeated pixels,
sparse padding or a synthetic large scene. Native blocks and GDAL cache are bounded;
whole-image mode intentionally materializes its input, as the reference does.

Stress sets `GDAL_CACHEMAX=64` and checks a 512 MiB RSS budget separately for each
real subprocess. Timings are retained observations, not a release performance
comparison. The compressed source does not establish multi-GiB TIFF offsets.

## Reproduction and retention

[Validation commands](../../README.md#p05-metadata-ingestion-and-optimization)
run offline after the P00 corpus/environment setup. HTTP checks require permission
to bind loopback. Final artifacts: `validation/artifacts/p05-qualified` and
`p05-qualified-python`; full raw captures and earlier investigation runs remain
under ignored `validation/artifacts/p05-*`. Reports, metadata pairs, selected
original image pairs, ordinary-size views, logs and hashes are retained here.
The large optimized TIFFs are reproducible and intentionally not versioned.

No AWS deployment, AL2023 requalification, non-Linux/non-x86_64 platform, real S3,
physical BigTIFF, non-SQLite backend, or browser E2E was run. Those existing
P06–P08/release gaps remain explicit. P05 adds a preparation-focused Python API;
it does not claim full `import terracotta` replacement or legacy context parity.
