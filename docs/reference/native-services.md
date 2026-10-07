# Native map discovery, values and tile grids

These interfaces require the `native` profile and work with ordinary SQL
catalogs, native keyed bands/expressions, STAC sources and the keyed projection
of advanced layers. HTTP, Python `NativeApplication`, WSGI and Lambda share the
same implementation. The named Terracotta profile returns 404 for these routes
and keeps its existing XYZ behavior.

## Discover a tileset

Request `GET /tilesets?page=0&limit=100`. Each entry includes an opaque `id`,
ordered `keys`, a `title`, a `tilejson` URL, a `point` URL and tile templates
associated with explicit matrix definitions. Follow these links rather than
constructing IDs. Pages contain at most `limit` entries, with a maximum of 1000.
Continue until a page contains fewer entries than requested. An empty catalog
returns an empty list. Discovery does not include source file locations.

`GET /tilejson/{id}.json` returns a raster [TileJSON 3.0](https://github.com/mapbox/tilejson-spec/tree/master/3.0.0)
manifest for `WebMercatorQuad`. It contains absolute XYZ tile URLs, WGS84
bounds, zooms 0–22, the dataset title and its stored attribution, or an empty
attribution when none is declared. The `tileSize` extension is always 256.
Zoom availability describes the renderer's supported range, rather than native
source resolution. Bounds are limited to Mercator latitude coverage; dateline
bounds are represented by a nonwrapping enclosing interval.

Supported style parameters are `stretch_range`, `colormap`,
`explicit_color_map` and `color_transform`, with the same interpretation as
native [singleband rendering](http.md). They are validated and copied into every
tile URL. Omitted parameters use stored dataset defaults, then the normal
metadata stretch. The `rcfp` extension identifies keys, source revision,
defaults, explicit parameters and the point-inspection URL. The revision is an
opaque current source identity, rather than a promise to retain older data.

Set `PUBLIC_URL = "https://maps.example.com/imagery"` in native TOML or
`TC_PUBLIC_URL` in the environment for HTTPS, reverse proxies, WSGI mounts and
API Gateway prefixes. Without it, absolute URLs use plain HTTP and the request's
`Host`. Forwarded headers are ignored. The URL must have no credentials, query
or fragment. Socketless Python requests need a `Host` header or `PUBLIC_URL`.
Configure tile CORS for the map application's origin. Discovery and point JSON
use metadata CORS.

The bundled `/examples/leaflet.html`, `/examples/maplibre.html` and
`/examples/openlayers.html` all consume these definitions in native mode.
With the public SDK:

```js
import { Client } from "/sdk/index.js";
import { leafletTileJSON } from "/sdk/integrations.js";

const client = new Client("https://maps.example.com/imagery");
await client.connect();
const { tilesets } = await client.discoverTilesets();
const tilejson = await client.tileJSON(tilesets[0].keys, {
  stretch_range: [0, 3000],
});
leafletTileJSON(L, tilejson).addTo(map);
```

`mapLibreTileJSONSource(tilejson)` supplies a raster source for MapLibre;
`openLayersTileJSONSource(ol, tilejson)` constructs OpenLayers' native TileJSON
source. Each uses the same manifest. The SDK continues to support the ordinary
keyed rendering adapters in both profiles.

## Inspect source values

`GET /point/{id}?lon=12.35&lat=45.46` accepts finite WGS84 longitude in
`[-180,180]` and latitude in `[-90,90]`, in longitude/latitude order. It returns
`coordinate`, `crs="OGC:CRS84"`, `interpolation="nearest"`, keys, `value`,
`valid`, `raw`, `scaled`, `values`, `units` and `provenance`.

The coordinate transforms into each source CRS and selects its containing
native affine cell. Pixel coordinates include the top/left boundary and exclude
the bottom/right boundary. No overview, display stretch, palette or interpolated
tile sample participates. Longitude is not wrapped. Invalid coordinates return
400; missing datasets return 404. A valid coordinate outside a source returns
200 with `valid=false` and null values, as do nodata, nonfinite samples, masks
and nonpositive or nonfinite alpha. Positive fractional alpha remains valid.

For a physical band, `raw` is the stored sample, `scaled` is
`raw * scale + offset`, and `value` follows that logical dataset's declared
`raw` or `scaled` mode. Calibration is applied after sampling. `scale`, `offset`
and descriptive `units` are returned without implicit unit conversion. Samples
cross the core as f64; integers beyond its exact range can lose precision.
Provenance includes the selected band, its name, source CRS and opaque revision,
with no file locations or credentials.

Persisted expressions evaluate each operand's nearest cell using its declared
value mode and existing dtype/mask rules. They return a computed `value`, null
`raw`/`scaled` and null units. Their provenance records the expression and operand
values/calibration. Advanced layer channels retain their declared expression,
selection and overlap behavior; their provenance lists observation IDs.

```js
const sample = await client.point(tilesets[0].keys, {
  longitude: 12.35,
  latitude: 45.46,
});
if (sample.valid) console.log(sample.value, sample.units);
```

This source query is separate from the SDK's existing `inspect`, which reads
displayed RGBA pixels.

## Explicit tile matrix sets

`GET /tileMatrixSets` lists grid IDs and definition links.
`GET /tileMatrixSets/{id}` returns output CRS, ordered axes, bounding extent and
23 matrices. Each matrix identifies its resolution (`cellSize`), scale
denominator, top-left origin, tile dimensions and matrix dimensions. Tile row
increases downward and column increases rightward. Coordinates use x/y order;
`WorldCRS84Quad` uses longitude/latitude even when the reader represents its CRS
as EPSG:4326. Resolution units are degrees for that grid and meters for the
projected grids. Scale denominators use the 0.28 mm reference pixel and the
WGS84 equatorial meters-per-degree conversion for the geographic grid.

| ID                  | Output CRS | Extent in output x/y units            | Matrix dimensions at zoom z |
| ------------------- | ---------- | ------------------------------------- | --------------------------- |
| `WebMercatorQuad`   | EPSG:3857  | ±π × 6378137 m on each axis           | 2^z × 2^z                   |
| `WorldCRS84Quad`    | OGC:CRS84  | [-180,-90,180,90] degrees             | 2^(z+1) × 2^z               |
| `RCFPArcticQuad`    | EPSG:3413  | [-8388608,-8388608,8388608,8388608] m | 2^z × 2^z                   |
| `RCFPAntarcticQuad` | EPSG:3031  | [-8388608,-8388608,8388608,8388608] m | 2^z × 2^z                   |

The global grids follow the [OGC tile matrix definitions](https://github.com/opengeospatial/2D-Tile-Matrix-Set/tree/master/registry/json).
The polar grids are RCFP definitions with exact power-of-two extents, rather than
the OGC UPS grids. Request `GET /tiles/{id}/{tileMatrixSet}/{z}/{x}/{y}.png` for a
fixed 256×256 tile. Integers must be within the selected matrix's dimensions.
Out-of-range rows/columns return 400. Dateline wrapping is client-owned, with
separate first/last columns. Source coverage outside the grid is clipped; cells
without valid source coverage are transparent. Continuous bands use
`REPROJECTION_METHOD`, categorical bands use nearest, and advanced channels
retain their declared sampling. Grid identity, CRS, geometry and sampling
participate in caches.

Tile responses support the same conditional requests and `revision` pinning as
other [rendered images](../how-to/rendered-caching.md). TileJSON style parameters
do not pin a rendered revision.

TileJSON discovery applies to Web Mercator. Geographic and polar clients must
use the explicit matrix descriptor and matching map projection. OpenLayers can
construct a `tilegrid.TileGrid` from `pointOfOrigin`, `cellSize`, tile dimensions
and matrix dimensions. Use a view in the descriptor's CRS; transforming other
browser layers into a polar CRS requires a separately configured projection
library.

Executed grid qualification uses real Arctic and southern source windows plus
both sides of the dateline. The southern stereographic case is at approximately
34°S, rather than an Antarctic acquisition. Support for a named grid does not
qualify every source CRS, resolution, overview or remote provider.

The matrix documents use the OGC Two Dimensional Tile Matrix Set 2.0 model.
TileJSON 3.0 is the claimed standard client interface. WMTS capabilities,
OGC API Tiles landing/conformance routes and service conformance are not provided.
