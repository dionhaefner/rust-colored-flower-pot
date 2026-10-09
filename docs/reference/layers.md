# Logical bands and derived datasets

Native keyed catalogs store ordered keys, raster bindings, inspected band
metadata, expressions and display defaults in SQLite. A dataset can refer to a
band in a packed TIFF, a separate file, or a persisted STAC selection. Clients
use the same keys, metadata and rendering APIs for each layout. Raster files
remain external, as with Terracotta.

Create these catalogs with `format="native"`. Their marker is `rcfp-native-2`.
Existing Terracotta catalogs retain their format and behavior. Native keyed
catalog writes currently require local SQLite. Read-only remote SQLite snapshots
use the regular catalog provider.

## Ingest existing imagery

Use the usual pattern-based ingest command. For a directory of separate bands:

```sh
rcfp ingest '/data/{scene}_{band}.tif' -o imagery.sqlite --format native
```

For packed multispectral TIFFs, map physical band indexes to logical names at
ingest time:

```sh
rcfp ingest '/data/{scene}.tif' -o imagery.sqlite \
  --bands red=3,green=2,blue=1,nir=4 --band-key band --values scaled
rcfp serve --source imagery.sqlite --bind 127.0.0.1:5000
```

Both catalogs expose `scene,band` keys. The packed file is read directly.
`--bands` implies native format and appends the named band key to the keys in
the pattern. Indexes are one-based. Invalid indexes, duplicate logical names
and incompatible catalog keys fail. An ingest batch is transactional.

Python uses the same catalog operations:

```python
import rcfp

store = rcfp.DataStore("imagery.sqlite")
store.create(["scene", "band"], format="native")
for name, index in {"red": 3, "green": 2, "blue": 1, "nir": 4}.items():
    store.insert(["scene1", name], "/data/scene1.tif", band=index,
                 values="scaled", stretch=[0, 1])

metadata = store.get_metadata(["scene1", "red"])
samples = store.get_raster_tile(["scene1", "red"], tile_size=(256, 256))
```

A separate-file registration uses the same `insert`, with band 1. Default values
are `raw`. Use `scaled` to apply the selected band's scale and offset. Optional
`stretch` and `colormap` become database defaults. Explicit request settings
override them. Without a stored stretch, rendering uses the measured range.
Metadata passed to `insert` must describe that logical dataset's values.
`skip_metadata=True` removes previous statistics and computes them when needed.
Reingest after changing raster contents or band declarations.

## Derived datasets

Persist expressions against keyed datasets:

```python
store.define(
    ["scene1", "ndvi"], "(nir-red)/(nir+red)",
    {"red": ["scene1", "red"], "nir": ["scene1", "nir"]},
    stretch=[-1, 1], colormap="viridis",
)
```

The derived dataset appears in normal discovery and uses the usual singleband
route. Input bindings, expression and defaults all live in the database.
Expressions use the shared restricted [grammar](../explanation/rendering.md).
Missing inputs, invalid syntax and cyclic dependencies fail before publication.
Derived statistics are sampled on a preview grid and identify their sampling
shape in metadata. Their footprint is the union of operand extents. The
`convex_hull` for these sampled products describes that extent, rather than an
exact valid-data boundary. Changes to input bindings invalidate derived arrays,
encoded results and sampled statistics.

## Common requests

For a `scene,band` catalog, clients use:

| Request                                            | Result                                  |
| -------------------------------------------------- | --------------------------------------- |
| `GET /keys`                                        | Ordered catalog keys                    |
| `GET /datasets`                                    | Logical keyed datasets                  |
| `GET /metadata/scene1/red`                         | Selected band's statistics and defaults |
| `GET /singleband/scene1/red/preview.png`           | Singleband preview                      |
| `GET /rgb/scene1/preview.png?r=red&g=green&b=blue` | RGB preview                             |
| `GET /singleband/scene1/ndvi/preview.png`          | Stored expression with its defaults     |

XYZ routes, request-time `/compute`, the viewer and JavaScript SDK use the same
contracts. RGB and compute retain the existing convention that the final key
selects a channel. Physical band selectors belong to ingestion. Native logical
datasets reject tile selectors other than band 1. Python `get_raster_tile`
accepts Web Mercator `tile_bounds`, `[height,width]` dimensions, and optional
`asynchronous=True` for a Future. Reuse a `DataStore` for array reads and a
`NativeApplication` for HTTP requests to retain workers and caches.

## Values, grids and validity

Inputs align on a common north-up Web Mercator grid before expressions. XYZ
requests supply that grid. RGB and compute previews use the union of input
extents. Continuous data uses `TC_REPROJECTION_METHOD`. Embedded categorical
bands use nearest sampling and require raw values. Raw expressions retain
source dtype arithmetic. Samples cross the core as f64, so integers beyond its
exact integer range can lose precision. Scaled values use `raw * scale + offset` in f64 after
alignment. Units are descriptive. No implicit unit or vertical conversion occurs.

Each selected band's validity combines nodata, nonfinite values, masks and
positive source alpha. Other bands' nodata does not erase its valid pixels.
Expressions retain operand masks. Masked RGB channels are zero and valid
channels remain visible. Selected bands, declarations, revisions, output grids
and local source generations participate in raster caching. Database defaults
and the resolved plan also participate in encoded-image caching.

STAC sources declare logical band names and calibration at discovery. See
[STAC ingestion](../how-to/stac.md#ingest-a-stac-selection) for persisted snapshots
and the direct read-only workflow.

## Import and rollback

Create a separate native database and reinsert the original catalog's keyed
raster paths. This preserves the key model and leaves the original intact:

```python
source = rcfp.DataStore("terracotta.sqlite")
target = rcfp.DataStore("native.sqlite")
target.create([key["key"] for key in source.get_keys()], format="native")
for dataset in source.get_datasets():
    target.insert(dataset["keys"], dataset["path"])
```

Import inspects referenced rasters and recomputes band-one raw metadata. Review
custom metadata and display defaults before switching the deployment. Rollback
means restarting with the original catalog. Neither profile selection nor
historical migrations upgrade catalogs to the native marker in place. The
Terracotta profile rejects native catalogs. Back up the database with SQLite's
backup API and preserve its referenced rasters. New band bindings and expressions
cannot be exported losslessly to Terracotta's older schema.

The `rcfp-native-1` format retains its [advanced layer API](advanced-layers.md)
for explicit overlap, category palettes and arbitrary EPSG grids. Its stored
channels are also discoverable as `layer,observation,channel` keyed datasets.
