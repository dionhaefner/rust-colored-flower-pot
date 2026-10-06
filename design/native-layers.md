# Native keyed datasets and raster plans

The public catalog model uses ordered keys and keyed datasets. Each dataset
resolves to a logical scalar channel. A persisted definition binds an inspected
asset and physical band, an expression over keyed datasets, or a dataset in a
persisted STAC snapshot. Storage layout is resolved before the common metadata,
array and tile interfaces. The final-key channel convention for RGB and compute
remains the existing Terracotta contract.

`rcfp-native-2` is a SQLite format built on the existing key, dataset, metadata
and transaction tables. `rcfp_definitions` holds band/expression/source bindings
and display defaults; `rcfp_sources` holds resolved STAC snapshots, source/search
configuration, provenance and content revisions. JSON values are database
columns. Managed serving requires the database and referenced rasters. External
credentials and signed-location refresh remain deployment responsibilities.
Native writes currently require local SQLite; the existing read-only remote
snapshot provider can open this format. Legacy catalog formats remain distinct,
and the Terracotta profile rejects both native markers. Import creates a new
catalog and preserves the original for rollback.

`src/catalog.rs` adapts SQL stores, native definitions, direct STAC selections
and `rcfp-native-1` layers. Direct STAC retains bounded startup discovery and
checksum-verified first-use TIFF staging. Multiband STAC names come from unique
common names or band names; single unnamed bands use the asset name. Persisted
sources retain resolved Items and absolute asset locations, so metadata documents
and revision manifests are unnecessary after ingestion. Staging is bounded per
selection. Applications retain at most 64 stored source generations to keep
staged filenames alive while worker handles may use them; further generations
require a restart. Imported credentials themselves are not stored.

A source-independent plan carries the selected band and raw/scaled semantics or
resolved expression inputs. Band plans reuse the native layer alignment and
expression core; adapters do not implement separate arithmetic or validity.
The internal grid specification derives from the request. Native RGB and compute
previews use a common union grid. Legacy SQL previews and numerical semantics
retain their qualified behavior. The advanced native layer API remains available
for explicit grids, overlap and categorical palettes; its defaults project into
`layer,observation,channel` discovery without changing its persisted format.

Each band is selected before warping. A coverage VRT combines finite samples,
the selected mask, nodata and positive alpha into binary density. Destination
coverage owns validity, so interpolation equal to a nodata sentinel remains
valid. Scale/offset apply after alignment in f64; raw expression arithmetic
retains source dtype. Categorical sources use nearest sampling and require raw
values. No implicit unit or vertical conversions occur.

Band metadata is measured from the selected source with the same validity and
calibration. Derived statistics are sampled on a bounded preview grid; their
extent polygon is explicitly documented as an extent rather than an exact
valid-data hull. Expressions reject missing inputs, invalid syntax and cycles
before publication. Database batches reuse existing transactions/savepoints.
Rebinding a dataset invalidates downstream resolved identities and sampled
statistics. Raster caches include selected bands, declarations, revisions,
CRS/resampling and local generations; encoded caches also include the resolved
plan and database display defaults.

Maintained checks are `tests/catalog_contracts.py` for common keyed workflows
and `tests/layer_contracts.py` for explicit alignment, overlap, masks and category
semantics. Existing public and pinned Terracotta contracts must be rerun against
the exact artifact being qualified.
