# HTTP API

Each deployment serves its specification at `/swagger.json` and a visual
explorer at `/apidoc`. Consult those endpoints for the running service's contract.
The [downloadable specification](../_generated/swagger.json) here is extracted
from the installed release documented by this site.

```{include} ../_generated/routes.md

```

## Dataset selection and images

Key order comes from `/keys`. Singleband and metadata paths contain all key
values. RGB/compute paths contain all except the final band key; `r`, `g`, and
`b` select RGB bands. Compute binds band names to `v1`–`v5` query parameters;
expressions use those variable names, for example `(v1-v2)/(v1+v2)` with
`v1=nir` and `v2=red`.
Previews end in `preview.png`; XYZ tiles use `{z}/{x}/{y}.png`.

JSON-valued query parameters include `tile_size`, `stretch_range`, per-channel
`r_range/g_range/b_range`, and `explicit_color_map`. Use a URL encoder. Stretch
endpoints accept supported numeric/percentile selectors; compute requires an
explicit stretch and expression. `color_transform` applies to supported image
routes. `colormap=explicit` requires an explicit mapping and forces nearest
category sampling. See [rendering recipes](../tutorials/imagery.md).

Metadata responses contain bounds, range, percentiles, footprint/statistics,
and attached metadata. Dataset queries support filtering and paging; this
release returns at most 1000 items per page. HTTP JSON shapes and status behavior
are distinct from the Python tuple/dict interfaces.

## Status, headers, and identity

Native mode interprets `p1`–`p99` as the 1st–99th stored percentiles and
`p0`/`p100` as minimum/maximum. It rejects reversed stretches, accepts constant
ranges, and honors requested `[height, width]` even for transparent out-of-bounds
tiles. Native `mod(a,b)` follows `%`. Bare `order_by=scene` selects the complete
key name; multiple keys use `order_by=[scene,band]`. Invalid or empty metadata
column lists return JSON 400 errors. POST `/metadata` accepts
`{"keys": [["scene", "red"], ["scene", "green"]]}` and returns metadata in that
order, optionally filtered by `columns`; `MAX_POST_METADATA_KEYS` bounds the batch.
The Terracotta profile retains its legacy behavior and specification. See
[native migration](../native-migration.md) before changing an existing client.

Images return `image/png` by default; native RGB requests with `format=webp` return
`image/webp`; discovery/metadata/specification return JSON. Native
request APIs also return cacheability and, for full requests, response headers.
CORS, content length, and protocol-version negotiation are part of the contract;
inspect the specification for endpoint-specific headers and validation.
`/__terracotta/identity` exposes embedded core implementation/protocol/database
identity. The core implementation version can differ from the wheel distribution
version reported by `rcfp.__version__` and `rcfp --version`, particularly in
development builds.
`/__terracotta/metrics` is a separate opt-in operational endpoint, not part of
the legacy published specification. See [errors and limits](errors.md).

## Native source bands and RGB display formats

Singleband XYZ tiles accept `band=N`. RGB XYZ tiles accept `r_band=N`,
`g_band=N`, and `b_band=N`, independently of the catalog selectors `r/g/b`.
Source indexes start at 1 and must exist in the TIFF. Catalog rows may point to
the same multiband asset; matching requests share its dataset and output grid.
Metadata remains based on band 1, so bands other than 1 need explicit numeric
`stretch_range` or `r_range/g_range/b_range`; a singleband explicit categorical
palette supplies its own value mapping. Source band selectors are supported
on tiles; previews use band 1. Source band selectors are native-only.

RGB previews and tiles accept `format=webp&quality=85` in native mode, returning
`image/webp` even though the route ends in `.png`. Quality is a number from 0 to
100; its default is 85. PNG remains the default. WebP changes display colors,
retains the same binary alpha coverage, and leaves analysis arrays unchanged.
It can substantially reduce transferred bytes while increasing server encode
CPU. Use it for visual RGB layers after reviewing their quality, especially when
client bandwidth dominates. Formats and quality participate in image-cache keys.
Custom color transforms work with both RGB formats. The bundled libwebp encoder
is required; external GDAL runtimes must supply that library too.
