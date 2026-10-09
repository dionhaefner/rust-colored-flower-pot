# HTTP API

Each deployment serves its specification at `/swagger.json` and a visual
explorer at `/apidoc`. Consult those endpoints for the running service's contract.
The [downloadable specification](../_generated/swagger.json) here is extracted
from the installed release documented by this site.

```{include} ../_generated/routes.md

```

## Dataset selection and images

Key order comes from `/keys`. Singleband and metadata paths contain all key
values. RGB/compute paths contain all except the final band key. `r`, `g`, and
`b` select RGB bands. Compute binds band names to `v1`–`v5` query parameters.
Expressions use those variable names, for example `(v1-v2)/(v1+v2)` with
`v1=nir` and `v2=red`.
Previews end in `preview.png`. XYZ tiles use `{z}/{x}/{y}.png`.

JSON-valued query parameters include `tile_size`, `stretch_range`, per-channel
`r_range/g_range/b_range`, and `explicit_color_map`. Use a URL encoder. Stretch
endpoints accept supported numeric/percentile selectors. Compute requires an
explicit stretch and expression. `color_transform` applies to supported image
routes. `colormap=explicit` requires an explicit mapping and forces nearest
category sampling. See [rendering recipes](../tutorials/imagery.md).

Metadata responses contain bounds, range, percentiles, footprint/statistics,
and attached metadata. Dataset queries support filtering and paging. This
release returns at most 1000 items per page. HTTP JSON shapes and status behavior
are distinct from the Python tuple/dict interfaces.

## Status, headers, and identity

Native mode interprets `p1`–`p99` as the 1st–99th stored percentiles and
`p0`/`p100` as minimum/maximum. It rejects reversed stretches, accepts constant
ranges, and honors requested `[height, width]` even for transparent out-of-bounds
tiles. Native `mod(a,b)` follows `%`. Bare `order_by=scene` selects the complete
key name. Multiple keys use `order_by=[scene,band]`. Invalid or empty metadata
column lists return JSON 400 errors. POST `/metadata` accepts
`{"keys": [["scene", "red"], ["scene", "green"]]}` and returns metadata in that
order, optionally filtered by `columns`. `MAX_POST_METADATA_KEYS` bounds the batch.
The Terracotta profile retains its legacy behavior and specification. See
[native migration](../native-migration.md) before changing an existing client.

Images return `image/png` by default. Native RGB requests with `format=webp` return
`image/webp`. Discovery/metadata/specification return JSON. Native
request APIs also return cacheability and, for full requests, response headers.
CORS, content length, and protocol-version negotiation are part of the contract.
Inspect the specification for endpoint-specific headers and validation.
`/__terracotta/identity` exposes embedded core implementation/protocol/database
identity and `compatibility_profile`. It follows metadata CORS policy so browser
SDKs can verify profile selection. Encoded path components are decoded after
route splitting, preserving literal slashes in catalog keys. The core implementation version can differ from the wheel distribution
version reported by `rcfp.__version__` and `rcfp --version`, particularly in
development builds.
`/__terracotta/metrics` and `/__terracotta/metrics/prometheus` are separate opt-in
operational endpoints, outside the legacy published specification. See
[monitoring](monitoring.md) and [errors and limits](errors.md).

## Native source bands and RGB display formats

Singleband XYZ tiles accept `band=N`. RGB XYZ tiles accept `r_band=N`,
`g_band=N`, and `b_band=N`, independently of the catalog selectors `r/g/b`.
Source indexes start at 1 and must exist in the TIFF. Catalog rows may point to
the same multiband asset. Matching requests share its dataset and output grid.
Metadata remains based on band 1, so bands other than 1 need explicit numeric
`stretch_range` or `r_range/g_range/b_range`. A singleband explicit categorical
palette supplies its own value mapping. Source band selectors are supported
on tiles. Previews use band 1. Source band selectors are native-only.

RGB previews and tiles accept `format=webp&quality=85` in native mode, returning
`image/webp` even though the route ends in `.png`. Quality is a number from 0 to 100. Its default is 85. PNG remains the default. WebP changes display colors,
retains the same binary alpha coverage, and leaves analysis arrays unchanged.
It can substantially reduce transferred bytes while increasing server encode
CPU. Use it for visual RGB layers after reviewing their quality, especially when
client bandwidth dominates. Formats and quality participate in image-cache keys.
Custom color transforms work with both RGB formats. The bundled libwebp encoder
is required. External GDAL runtimes must supply that library too.

A native layer database exposes `/layers`, `/layers/{id}`, and
`POST /layers/{id}/render` through the same application. See
[advanced layer contracts](advanced-layers.md) for explicit grids and overlap.
Native [keyed datasets](layers.md) use the regular discovery, metadata, singleband,
RGB and compute routes. Band bindings and default styles are stored in the database.

## STAC sources

`rcfp serve --source SOURCE` opens STAC documents and source configurations as a dataset
catalog through the same routes above. Keys are `collection`, `datetime`, `item`
and `asset`. Singleband, RGB, compute, palettes, stretch and source-band selectors
use the existing request parameters. The viewer and SDK use normal catalog
discovery. These sources require the native profile.

Metadata statistics and render values include per-band STAC scale and offset.
The standard metadata object's `metadata.stac` contains the public asset
and observation descriptor, alongside `selection_id`, `search_id` and
`selection_metrics`. Service identity includes `source_revision`.

Discovery completes before listening. The first metadata or render request
stages and verifies the whole TIFF and measures first-band statistics. Later
requests reuse the verified file. See [serving STAC](../how-to/stac.md) for
commands, credentials, revision requirements, dataset keys and download limits.

## Native tileset discovery and inspection

Use [native map services](native-services.md) for client-ready TileJSON, source
values and explicit geographic/polar tile grids. `/tilesets` supplies opaque IDs
and URLs across supported catalog layouts. The native-only routes share the
HTTP, Python, WSGI and Lambda core and appear in the running `/swagger.json`.
