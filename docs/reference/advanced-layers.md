# Advanced native layers

Native layer catalogs keep assets, band metadata, observations, selection,
overlap policy and default rendering specifications together in one SQLite
database. Raster files remain external, as in Terracotta. The format marker is
`rcfp-native-1`; this format is available
in the native profile and currently supports local SQLite databases.

## Register and render an existing RGB file

```sh
rcfp layers create imagery.sqlite
rcfp layers register imagery.sqlite aerial /data/aerial.tif
rcfp layers render imagery.sqlite aerial -o preview.png
rcfp serve -d imagery.sqlite --bind 127.0.0.1:5000
```

Registration uses the file's red/green/blue color interpretations when present,
otherwise bands 1/2/3 for files with at least three bands and band 1 for smaller
files. `--bands 3,2,1` overrides this order. Byte imagery defaults to the numeric
stretch `[0,255]`; other dtypes require `--stretch MIN,MAX`. The stored default
render uses the complete source extent on a 256×256 EPSG:3857 grid, bilinear
sampling, raw values, PNG and any-valid coverage. It can be replaced through
`LayerStore.put`. Existing files are read directly; registration never splits
or rewrites their bands.

```python
import rcfp

store = rcfp.LayerStore("imagery.sqlite")
store.create()
store.register("aerial", "/data/aerial.tif", bands=[3, 2, 1])
app = rcfp.LayerApplication(store.path)
with open("preview.png", "wb") as stream:
    stream.write(app.render("aerial"))
```

`LayerStore.get(name)` returns the complete stored definition, `list()` returns
IDs, `delete(name)` removes a layer, and `put(name, definition)` inspects every
asset and replaces the definition in one transaction. Inspection failure
preserves the previous definition. IDs contain ASCII letters, digits, `_` or
`-` and have at most 256 characters. Relative local raster locations resolve
at registration and are stored as absolute paths.

## Observations and band selection

A layer contains 1–64 ordered `observations`, an `overlap` policy, optional
`selection` and optional `default_render`. Each observation has a unique `id`,
1–32 named `assets` and arbitrary JSON `attributes`. Selection compares each
requested attribute with exact JSON equality. A selection matching nothing
fails clearly.

An optional `mosaic` budget enables indexed spatial and exact attribute selection
for up to 100,000 observations on EPSG:3857 output grids. Oversized views fail
before raster reads. See [virtual mosaics](../how-to/mosaics.md) for limits,
contributor ownership and reproducible broad-view snapshots.

Each asset has `location`, optional immutable `revision`, optional
`band_names` mapping aliases to one-based indexes, and optional
`categorical_bands` indexes. Registration inspects its metadata and stores:

- CRS, `[height,width]`, GDAL affine transform and four source-CRS footprint corners.
- Each band's one-based `index`, description `name`, `dtype`, GDAL `data_type`,
  `units`, `scale`, `offset`, `nodata`, color interpretation, mask flags,
  category names and RGBA color table.

The footprint describes the affine raster extent, not a valid-data hull.
Nonfinite nodata is represented as a string. Band names resolve first through
explicit aliases and then through unique nonempty GDAL descriptions. Unknown,
ambiguous, zero, negative and out-of-range selections fail. Complex bands are
unsupported. Re-register after changing band declarations. Remote revisions
must use immutable URLs; revision changes invalidate application caches and
handles but do not clear GDAL's downloaded-byte cache.

For separate-file RGB, register an observation with assets named `red`, `green`
and `blue`, then bind each to band 1. For a multispectral file, register one
asset and bind its red/NIR bands by name or index. Asset names have no relation
to the position or meaning of Terracotta catalog keys.

## Render specifications

The same specification is accepted by `LayerApplication.render(name, spec)`,
`samples(name, spec)`, the native HTTP route and CLI `layers render --spec`.
The CLI file is optional request input; the service's configuration and default
render are stored in the database.

```python
spec = {
    "bindings": {
        "red": {"asset": "image", "band": 3},
        "nir": {"asset": "image", "band": "nir"},
    },
    "values": "scaled",
    "channels": [{
        "expression": "(nir-red)/(nir+red)",
        "stretch": [-1, 1],
    }],
    "coverage": "any_valid",
    "grid": {
        "crs": "EPSG:32632",
        "bounds": [759960, 5037380, 765080, 5042500],
        "size": [256, 256],
        "resampling": "bilinear",
    },
    "encoding": "png",
}
```

`bindings` maps 1–16 expression identifiers to asset/band pairs. Every selected
observation must supply those bindings. `channels` contains one scalar channel
or three channels in red/green/blue order. Expressions use the shared restricted
[expression grammar](../explanation/rendering.md); they cannot execute arbitrary
code. Every continuous channel requires a finite increasing numeric `stretch`.
The output maps valid stretched values to 1–255, using ties-to-even rounding;
masked channels are zero.

`values="raw"` retains source dtype arithmetic, including integer wrap/promotion.
Use `scaled` for physical-value arithmetic: alignment precedes
`raw * scale + offset` in f64, with defaults 1 and 0. Unit conversion and
vertical value shifts are never implicit; units are metadata and scale/offset
are the declared numeric transformation. Public sample arrays are
f64 and carry one mask per output channel. Integer values beyond f64's exact
integer range cannot be represented exactly in these arrays.

`grid` specifies an EPSG CRS, increasing `[west,south,east,north]` bounds in that
CRS's coordinate units, `[height,width]` dimensions from 1 to 2048, and
`nearest`, `bilinear`, `cubic` or `average` resampling. Axes use traditional
x/y order, including longitude/latitude for EPSG:4326. Every asset is deliberately
aligned to this same north-up grid before expression evaluation. Different source
CRSs and resolutions are supported. Geographic bounds crossing the antimeridian
must be split into separate increasing grids.

Each input's mask combines its band's nodata, nonfinite samples, mask and
positive source alpha. Alpha is binary coverage: any positive finite alpha,
including a fractional value below one, is valid. Per-band validity remains
independent during warping and expressions. `first_valid` takes the first valid
computed sample in observation order; `last_valid` takes the last. This happens
per output channel. `coverage="any_valid"` makes a pixel opaque if any output
channel is valid; `all_valid` requires every channel. PNG uses explicit alpha.

Categorical bands use nearest sampling even if another resampler is requested,
and require raw values. Set `categorical_bands` for rasters that lack embedded
category declarations. A single categorical output channel uses
`categories=[{"value": 10, "color": [0,128,0,255]}, ...]` and `stretch=None`.
At most 255 distinct finite values are supported; unmatched classes are
transparent. Palette entries match exact computed values, never a continuous
stretch. PNG compression bytes are not a numerical or category contract.

## HTTP and CLI

Run `rcfp serve -d imagery.sqlite`. The service exposes:

| Route                      | Contract                                                           |
| -------------------------- | ------------------------------------------------------------------ |
| `GET /layers`              | Format marker and ordered layer IDs                                |
| `GET /layers/{id}`         | Stored layer, observations, assets and band metadata               |
| `POST /layers/{id}/render` | PNG from a JSON render specification; `{}` uses the stored default |

Discovery also supports HEAD. OPTIONS advertises allowed methods. Render
requests require `Content-Type: application/json`; query parameters and unknown
fields are rejected. File locations cannot be supplied by HTTP render requests.
Invalid specifications return 400, missing layers 404, unsupported methods 405,
overload 503 and raster deadlines 504. Active GDAL jobs continue after a deadline.
Body, admission and worker bounds apply as for the other native interfaces.
`/swagger.json` documents the native routes; compatibility-mode specifications
retain their existing contract. Stored default channels also appear through regular keyed discovery as
`layer,observation,channel` datasets. The explicit layer routes retain arbitrary
grids, overlap and coverage settings.

CLI administration includes `layers create|list DATABASE`,
`layers describe|delete DATABASE ID`, and `layers put DATABASE ID`, which reads
one complete layer definition from standard input. `layers render DATABASE ID
-o IMAGE` uses the stored default; `--spec REQUEST.json` overrides it for that
render. No render file is required for a stored default.

## Import and rollback

Keep a backup of the original catalog. Create a separate native database and
import explicit asset names with full ordered dataset keys:

```python
store = rcfp.LayerStore("native.sqlite")
store.create()
store.import_catalog(
    "scene", "terracotta.sqlite",
    {"red": ["scene", "red"], "green": ["scene", "green"], "blue": ["scene", "blue"]},
)
```

The CLI equivalent is `rcfp layers import SOURCE DESTINATION ID BINDINGS_JSON`.
Import copies paths into one observation and inspects the rasters. Add a default
render with `get`/`put` or supply a rendering specification per call. Import never
modifies the source catalog and never assumes that its final key denotes a band.
Legacy SQL providers supported by the catalog facade can serve as import sources;
remote snapshots must already contain their normal metadata.

The native marker is rejected by legacy catalog APIs and the Terracotta profile.
Existing databases are never upgraded to the native marker in place. Rollback
means restarting with the original Terracotta catalog. Native definitions cannot
be losslessly exported to that older schema: selected bands, expressions and
observation policies exceed its model. Back up the native SQLite database with
SQLite's backup API, together with the external rasters it references.
