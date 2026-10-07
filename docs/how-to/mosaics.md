# Virtual mosaics and broad-view snapshots

An advanced native layer can combine adjacent or overlapping rasters while
bounding the work of each request. Enable indexed mosaics with a `mosaic` budget
in a [layer definition](../reference/advanced-layers.md). Registration stores
Web Mercator footprint bounds in a SQLite RTree and exact observation attributes
in a separate index. Raster files remain external.

```python
import rcfp

store = rcfp.LayerStore("terrain.sqlite")
store.create()
store.put("terrain", {
    "observations": [
        {"id": "west", "assets": {"height": {"location": "/data/west.tif"}},
         "attributes": {"date": "2025-01-01"}},
        {"id": "east", "assets": {"height": {"location": "/data/east.tif"}},
         "attributes": {"date": "2025-01-01"}},
    ],
    "overlap": "first_valid",
    "mosaic": {
        "max_sources": 8, "max_reads": 16,
        "max_buffer_bytes": 67108864, "max_source_bytes": 1073741824,
    },
})
```

Each observation represents one contributor, with matching named assets for the
requested bindings. Registration supports up to 100,000 observations. Requests
use an EPSG:3857 grid. The spatial index conservatively selects observations
whose asset extents intersect that grid. It loads at most `max_sources` candidates
plus one overflow row, in declared observation order. `selection` in a request
matches exact JSON attributes and narrows any stored layer selection. Use an
explicit date or interval label; the service does not infer a preferred date,
interpolate time, or interpret date strings as ranges.

```python
spec = {
    "selection": {"date": "2025-01-01"},
    "bindings": {"h": {"asset": "height", "band": 1}},
    "values": "scaled",
    "channels": [{"expression": "h", "stretch": [-100, 3000]}],
    "coverage": "any_valid",
    "grid": {"crs": "EPSG:3857", "bounds": [0, 0, 10000, 10000],
             "size": [256, 256], "resampling": "nearest"},
    "encoding": "png",
}
app = rcfp.LayerApplication(store.path)
plan = app.plan("terrain", spec)
image = app.render("terrain", spec)
arrays, contributors, owners = app.samples("terrain", spec, provenance=True)
```

`plan` returns ordered observation IDs, `source_count`, `reads`, `buffer_bytes`
and `source_bytes` without opening rasters. `owners` contains one integer array
per output channel. A valid pixel indexes `contributors`; `-1` denotes invalid
output. Contributors identify observations in the stored definition, including
their assets and declared revisions. Selected observations can own no valid
pixels. Each channel follows its own validity and
overlap rule. An empty spatial or temporal selection yields transparent output
and invalid samples.

Use `POST /layers/{id}/plan` with the same JSON accepted by
`POST /layers/{id}/render`. Native CLI `data-operation` with
`operation="layer_samples"` and `provenance=true` returns `samples`,
`contributors` and flattened ownership arrays, with JSON `null` for invalid
pixels. Ordinary sample calls retain their existing return shape.

## Budgets and exhausted views

`max_sources` bounds candidate observations and their distinct bound asset
references. `max_reads` bounds band-read requests, including repeated bindings.
Limits are checked before raster reads and before output buffers are allocated.
No candidate subset is silently rendered when a view exceeds its budget. HTTP
returns **422** with `mosaic budget exceeded`; Python and CLI raise an error.
Worker/admission overload remains **503**, and raster deadlines remain **504**.

`buffer_bytes` is a conservative reservation based on grid dimensions, bindings,
channels and expression length, covering inputs, expression temporaries,
ownership, normalization and encoding. `source_bytes` sums full uncompressed
source-band sizes plus mask storage for each read, rather than predicting how
many compressed blocks an overview will decode. This deliberately rejects some
large COG reads that might be inexpensive. It avoids treating per-file overviews
as a guarantee of bounded broad-view cost. Bounds are 1–64 sources, 1–1024 reads,
512 MiB of request buffers and 64 GiB of uncompressed source estimates.

These estimates are admission limits, not total process RSS or network-byte
limits. Configure [GDAL, worker and application caches](resources.md) separately.
The existing fixed worker queue and application admission bound concurrent work;
active GDAL work drains after a response deadline. Read errors fail the complete
request. The index contains affine extent bounds, not exact valid-data hulls.
Re-register after changing source georeferencing, band declarations or selection.
Selected local files and sidecars must match their registered generation or the
request fails before reads. Keep sources stable during registration and serving;
the footprint index represents the registered extent. Remote sources require
immutable revisioned URLs.

## Prepare an optional broad-view product

A collection-level snapshot can replace many source opens with one raster read.
Preparation requires NumPy, Rasterio and the native executable in an operator
environment. Run the helper from a source checkout or unpacked source archive.
It processes bounded windows and reuses the layer reader:

```sh
python packaging/prepare-mosaic.py --binary /path/to/rcfp prepare \
  terrain.sqlite terrain --spec terrain-grid.json --factor 4 \
  --aggregation average --block-size 128 --out prepared-terrain
python packaging/prepare-mosaic.py --binary /path/to/rcfp check \
  prepared-terrain/manifest.json
rcfp layers put terrain.sqlite terrain-broad < prepared-terrain/layer.json
```

The specification fixes the full base grid and scientific channels. Base
sampling must be nearest on an EPSG:3857 grid. The shape must be divisible by
`factor`. Each base window must satisfy the original mosaic budgets; preparation
fails if its candidate density is too high. Choose a smaller block or partition
the collection deliberately. The default total preparation limit is 67,108,864
base pixels, adjustable with `--max-pixels`. Factors are 1–32, and base blocks
fit the existing 2048-pixel dimension limit. Preparation only accepts local,
self-contained TIFFs without mask, overview or auxiliary sidecars.

The product uses Float64 values and per-band NaN nodata on the reduced grid.
Processing order is explicit:

1. Align each observation's bands to the base grid and apply declared calibration.
2. Evaluate each channel expression within that observation.
3. Apply ordered first/last-valid overlap separately to each channel.
4. Reduce the composited channel with the declared aggregation.

`average` takes the unweighted mean of valid base samples within each factor
block. Empty blocks remain invalid. Thus an index is computed **before** spatial
averaging; averaging its input bands first is a different product and is not
supported by this helper. Categorical inputs or palettes reject `average`.
`nearest` selects the base sample at `[factor//2, factor//2]` in each block,
including that sample's validity. It preserves category IDs and does not choose
another valid category when the center is invalid. There is no implicit mode,
majority vote, unit conversion or temporal aggregation.

`manifest.json` records the source definition digest, full SHA-256 checksums of
parent TIFFs, specification, factor, aggregation, generation time, output checksum
and block/channel contributor counts. `layer.json` contains a separate layer
binding those computed channels. Counts trace blocks to source observations;
original mosaics additionally support per-pixel ownership. Failed preparation
publishes neither a manifest nor a usable layer definition. PNGs and service
responses are never preparation references.

## Freshness, replacement and view selection

Products are immutable snapshots. Serving them does not follow later source
changes automatically. Run `check` before registration or deployment and when
sources change. It rejects changed definitions, calibration, overlap, selection,
parent bytes or product bytes. Checks hash the parents and belong outside the
latency-sensitive request path. Regenerate into a new directory, verify it, and
atomically replace the broad layer with `LayerStore.put` or `layers put`. Preserve
the old directory for rollback. The new asset checksum participates in raster
and encoded-image identities; local file generations invalidate caches as usual.

Choose the original or broad layer explicitly in the application. Use the
original for detailed views; use the prepared product when output pixels are at
least as large as its stored pixels. The product is a distinct scientific
sampling choice, so the service does not switch to it implicitly or substitute
it after budget exhaustion. Its stored expressions operate on already computed
channels; changing scientific expressions requires regeneration. Display
stretches and colors can still be changed at rendering time.

Compatibility catalogs and Terracotta routes retain their existing single-dataset
selection and rendering. Indexed mosaics and preparation are native layer
features. The maintained correctness and measurement runner is
`tests/mosaic_contracts.py`; its reports distinguish loopback/local evidence
from cloud, WAN, browser-paint and other-platform qualification.
