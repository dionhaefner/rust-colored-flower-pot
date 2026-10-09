# Serve a STAC catalog

Use `rcfp serve --source SOURCE` to open a catalog and `rcfp connect` to open
its interactive map. The same entry point accepts a SQL catalog location, a
local or HTTPS STAC document, or a local source configuration file. STAC sources
require the native profile.

The server discovers dataset keys and exposes the regular metadata, singleband,
RGB and compute routes. Tile clients and the viewer use these contracts for every
source. Logical channel names select datasets. Operators bind physical bands and
calibration at discovery or ingestion. Clients choose stretch and palettes through
regular requests. Managed catalogs also store display defaults in the database.

## Serve an entire static catalog

Point the server at a Catalog or Collection entry point, either a local JSON
file or an HTTPS URL:

```sh
rcfp serve --source docs/examples/data/lagoon-catalog.json \
  --bind 127.0.0.1:5000
```

In another terminal:

```sh
rcfp connect http://127.0.0.1:5000
```

The included catalog contains two Items representing the red and green bands
of the same Sentinel acquisition. Switch between them in the Dataset menu.
Your own catalog may contain nested Catalogs/Collections and many observations.
RCFP traverses all reachable `child` and `item` links before listening, resolves
relative links against each consumed document, and orders the selected assets
by time and identity. Shared documents and cycles are visited once. `parent`,
`root`, `self` and unrelated links are not followed.

Remote metadata links must stay on the entry point's origin. Local catalogs
must link to local metadata documents. Asset files can be hosted separately.
Each selected asset needs a content revision as described below, and raster
downloads begin when metadata or rendering first needs an asset. Tiles use the resolved selection.
They never walk the catalog again. Restart to pick up catalog changes.

This operates directly on the STAC metadata without importing a database.
For large catalogs, an indexed STAC API can select relevant observations without
downloading every Item document. Discovery limits below apply to both workflows.

## Try a multispectral Item

From the repository or source archive, after installing RCFP, start the server:

```sh
rcfp serve --source docs/examples/data/lagoon-multispectral-stac.json \
  --bind 127.0.0.1:5000
```

In another terminal, open the map:

```sh
rcfp connect http://127.0.0.1:5000
```

The example supplies checksum-pinned Sentinel-2 red, green, blue and near-infrared
crops as assets of one Item. The regular viewer supports
pan, zoom, asset selection, singleband and RGB rendering, stretch, color maps,
expressions, share links and display-pixel inspection. Choose display settings
in the map or in render requests. Stretch endpoints use physical units after
scale and offset.

Replace the example with your own local Item or ItemCollection JSON file, or an
HTTPS document URL. Relative asset links resolve against that document.
Restart the server to update its resolved catalog. Each render selects one
observation. Observations are not mosaicked or blended.

## Ingest a STAC selection

Import a static source into a managed database:

```sh
rcfp ingest --source docs/examples/data/lagoon-multispectral-stac.json \
  -o imagery.sqlite
rcfp serve --source imagery.sqlite --bind 127.0.0.1:5000
```

The default keys are `collection,datetime,item,asset`, matching direct STAC
serving. The database contains the resolved Items, absolute asset locations,
checksums, band declarations, source definition and metadata. Raster files remain
external. After ingestion, serving uses this snapshot without rereading the
source metadata documents. Reingest to publish a new selection. Ingestion
computes selected-band metadata before atomic publication. `--skip-metadata`
defers raster staging and statistics until needed.

Import an indexed POST Item Search with an inline query:

```sh
rcfp ingest --source https://your-stac-service.example/search \
  --search '{"collections":["sentinel-2-l2a"],"bbox":[12.3,45.4,12.4,45.5]}' \
  -o imagery.sqlite
```

Python uses the same keyed API:

```python
import rcfp

store = rcfp.DataStore("imagery.sqlite")
store.create(["scene", "band"], format="native")
store.ingest("catalog.json", key_mapping={"scene": "item", "band": "asset"})
store.define(["lagoon", "ndvi"], "(nir-red)/(nir+red)",
             {"nir": ["lagoon", "nir"], "red": ["lagoon", "red"]},
             stretch=[-1, 1], colormap="viridis")
```

Key mappings refer to `collection`, `datetime`, `item` or `asset`. Duplicate
mapped dataset keys fail. Keep enough observation keys to distinguish the
selected Items. Packed TIFFs need unique logical band names in `eo:bands`.
Common names take precedence over names. An unnamed single band uses its asset
name. The regular tile requests use these logical names without physical band
parameters. STAC calibration supplies scaled values. Local raster registrations
can declare the same semantics with `values="scaled"`.

## Search an existing STAC API

Direct read-only discovery remains available when a managed database is
unnecessary. The provider performs spatial/time filtering and pagination,
including for an existing PgSTAC-backed API:

```sh
rcfp serve --stac-search https://your-stac-service.example/search \
  --stac-collections sentinel-2-l2a --stac-bbox 12.3,45.4,12.4,45.5 \
  --bind 127.0.0.1:5000
```

RCFP completes the bounded search before listening and retains the selection for
the server's lifetime. Tiles use that selection. Restart to refresh a direct
source. RCFP does not execute PgSTAC SQL directly.

Managed ingestion accepts `--source-options JSON` or Python's `options` argument
for the deployment settings below. Direct serving exposes the corresponding
`--stac-*` options. A local `rcfp-source` configuration file remains an optional
way to share direct discovery settings across CLI, Python, WSGI and Lambda.
`--database` remains an alias for `--source`.

| Option             | Value                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------- |
| `headers`          | Path to a credential-header mapping for discovery.                                          |
| `asset_urls`       | Path to a signed-asset URL mapping refreshed by the deployment.                             |
| `revisions`        | Path to SHA-256 revisions when Items omit checksums. Ingestion persists resolved checksums. |
| `assets`           | Optional array of physical asset names to discover.                                         |
| `max_items`        | Maximum returned Items. Default 1000, maximum 10,000.                                       |
| `max_documents`    | Maximum static catalog documents. Default 10,000, maximum 20,000.                           |
| `max_staged_bytes` | Verified TIFF staging budget per selection. Default 512 MiB, maximum 16 GiB.                |

Unknown options fail. Credentials and refreshed signed locations remain operational
inputs. Public metadata excludes asset URLs and credential headers.

### Try Earth Search

The included source configuration queries
[Element 84's Earth Search](https://github.com/Element84/earth-search) for two
Sentinel-2 observations over Venice on June 29 and July 2, 2021:

```sh
rcfp serve --source docs/examples/data/earth-search-source.json \
  --bind 127.0.0.1:5000
```

Then run `rcfp connect http://127.0.0.1:5000` and switch observations in the Dataset
menu. This example uses a real HTTPS Item Search endpoint with bbox/time filters
and one Item per page. It exposes the 60 m `coastal` band to keep the total raster
transfer around 6.7 MB. The companion `earth-search-revisions.json` pins the
original provider TIFFs. Its query includes two known Item IDs so the example's
selected observations and revision mapping agree. Rendering and map controls use
the same public API as other catalogs. Change the query and supply revisions for
your own selection.

### Asset revisions

Every selected asset needs a trusted SHA-256 content digest. The STAC File
extension's `file:checksum` is accepted when it contains `1220` followed by the
64-character hexadecimal digest. If the provider omits it, set
`options.revisions` to `"revisions.json"` with a mapping like:

```json
{
  "sentinel/lagoon/red": "3e268183775bbcdc013eb142e8776b6c9eda17099bed33f8db368049eed07be7"
}
```

Keys have the form `collection/item/asset`. Missing or invalid revisions fail
startup. Item timestamps, ETags and signed URLs do not establish immutable file
content. RCFP verifies the digest before using raster bytes.

### Private providers and signed assets

Set `options.headers` to `"headers.json"` for search or remote metadata credentials:

```json
{ "Authorization": "Bearer YOUR_CURRENT_TOKEN" }
```

`Authorization` and `X-API-Key` are supported. For assets requiring signed URLs,
set `options.asset_urls` to `"asset-urls.json"`:

```json
{ "sentinel/lagoon/red": "https://your-storage.example/red.tif?signature=..." }
```

An external credential or signing process can replace these files with current
values. RCFP rereads the headers before discovery and the asset URLs before
first use, and retries once with a fresh file read after an upstream 401/403.
It does not generate cloud credentials or signatures. Keep these files private.
Source links, signatures and credential headers are excluded from public
metadata and upstream error messages. Refreshing a signed URL cannot replace
the bytes associated with a verified revision.

## Download costs and limits

On an asset's first metadata or render request, RCFP copies or downloads the
**whole self-contained TIFF**, verifies it, and stages it privately. It computes
first-band statistics for metadata and the default stretch. Subsequent requests
reuse the verified file and cached metadata. Rendering uses the bounded raster
executor. Embedded georeferencing, masks, alpha and
nodata remain authoritative. External sidecars and VRT source graphs are not
supported. Remote reads require HTTPS. Literal loopback HTTP is allowed for
local adapters and tests. An `s3:` reference needs a signed HTTPS URL mapping.

The default cumulative staging budget is 512 MiB. Set
`options.max_staged_bytes` to change it, up to 16 GiB. This budget covers all
assets used during the server's lifetime.
First-use download latency can therefore be substantial for large assets.
First-band statistics use `TC_LAZY_LOADING_MAX_SHAPE` (default `[1024,1024]`).
The in-memory metadata cache retains up to 256 asset descriptors.

API discovery defaults to 100 Items per page, 100 pages and 1000 returned Items.
`options.max_items` changes the result limit up to 10,000. Each page or Item
JSON document is bounded to 8 MiB and a search to 32 MiB. Limits fail explicitly.
Results are never silently truncated. Requests have a 30-second transport timeout.
All pages must succeed before startup. Redirects, cross-origin pagination,
malformed responses and conflicting duplicate Items fail the selection.

Static discovery shares the 1000-Item default, `options.max_items` setting, and
32 MiB total metadata budget. Its document limit defaults to 10,000.
`options.max_documents` changes it up to 20,000, counting Catalogs, Collections
and Items. Each document is limited to 8 MiB. Missing or invalid linked documents
fail startup without exposing a partial selection. Catalog links must use GET
without link-supplied headers. Discovery metadata reports the document count
in `metrics.pages`. An API source reports its response page count there.

Assets are ordered by UTC start time, then collection, Item ID and asset key.
Identical duplicate observations collapse, but duplicate Item occurrences still
count toward the ingress limit. Empty selections start successfully and display
an empty-state message in the map.

## Discover datasets and render them

STAC sources expose the same `/keys`, `/datasets`, `/metadata`, `/singleband`,
`/rgb`, `/compute` and `/colormap` contracts as database sources. The JavaScript
SDK and viewer use the same discovery and rendering code. Dataset keys are
`collection`, `datetime`, `item`, and `asset`, with asset last so RGB and compute
can select multiple assets from one observation. URL-encode each key separately.
The datetime key is the UTC start time, including for interval observations.
Items without a collection use `~`. Collection names starting with `~` gain one
leading `~` so the mapping stays unique.

For the included red Item, request a preview with:

```sh
curl 'http://127.0.0.1:5000/singleband/sentinel/2021-06-29T10%3A00%3A00Z/lagoon/red/preview.png?stretch_range=[0,2000]&colormap=viridis' \
  --globoff --output preview.png
```

Omit `stretch_range` to use the measured first-band range. Tile routes replace
`preview.png` with `{z}/{x}/{y}.png`. Color maps, categorical rendering, transforms,
rectangular tile sizes, RGB PNG/WebP and expressions follow the
[HTTP reference](../reference/http.md). A multiband asset accepts `band=N` on a
tile request with an explicit numeric `stretch_range`. RGB uses `r`, `g`, `b`
asset keys and optional `r_band`, `g_band`, `b_band` plus channel ranges. Assets
must share the collection, datetime and Item keys to be composed in one request.

For an Item with red, green and blue assets, the RGB template is:

```text
/rgb/{collection}/{datetime}/{item}/{z}/{x}/{y}.png?r=red&g=green&b=blue
```

By default, discovery includes assets declaring TIFF media types. When media
type is absent, `.tif`/`.tiff` hrefs or `raster:bands` identify raster candidates.
Non-TIFF media types such as PNG thumbnails are skipped. An Item may expose at
most 16 raster assets. Optional `"assets": ["red", "green"]` in source options restricts the catalog
to named assets. Absent names are skipped. It does not select a rendering.

`/metadata/{collection}/{datetime}/{item}/{asset}` returns the standard raster
statistics in scaled units and a `metadata.stac` descriptor containing observation,
revision, footprint, projection and band information. Its `metadata` object also
contains `selection_id`, `search_id` and `selection_metrics`. These discovery
metrics exclude raster downloads. `/keys` and `/datasets` read no raster bytes.
The service identity includes a generic `source_revision` for share links.

The selection identity hashes ordered revisions and rendering/discovery metadata,
excluding private locations and signatures. A separate `search_id` hashes the
provider, normalized query and selection options. Metadata and render requests
use the common methods, CORS policies, validation and cache behavior.

## Fields and rendering semantics

| STAC field                                                        | RCFP mapping                                                                                                                                                    |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type`, `stac_version`, `id`, `collection`                        | Feature Items from STAC 1.0.0/1.1.0. Collection plus Item ID identifies an observation.                                                                         |
| `properties.datetime`                                             | UTC-normalized observation instant.                                                                                                                             |
| `start_datetime`, `end_datetime` when datetime is null            | UTC-normalized closed observation interval. Ordering uses its start.                                                                                            |
| `bbox`, `geometry`                                                | Two-dimensional WGS84 bbox and Polygon/MultiPolygon discovery footprint, retained in public metadata. Dateline-crossing bboxes are accepted.                    |
| Asset key, `href`                                                 | Private native source location. Requested keys absent from an Item are skipped. Default TIFF discovery or an optional asset allowlist.                          |
| `file:checksum` or explicit `revisions`                           | Verified SHA-256 content revision.                                                                                                                              |
| `raster:bands`                                                    | Array position becomes one-based band. `scale` defaults to 1 and `offset` to 0, applied after alignment in f64. Nonfinite results are invalid.                  |
| `raster:bands[].nodata`                                           | Numeric or `nan`/`inf`/`-inf` declaration. If present it must match embedded GDAL nodata before interpolation. Embedded masks/alpha/nodata govern coverage.     |
| `eo:bands`                                                        | Band `name` and `common_name` aliases in metadata. Select rendering bands by index. If both EO/raster arrays exist their lengths must match.                    |
| Projection `proj:code`, `proj:epsg`, `proj:wkt2`, `proj:projjson` | Discovery CRS descriptor, asset values taking precedence over Item values. Actual raster georeferencing remains authoritative. Declarations do not override it. |
| Search `bbox`, `datetime`, `collections`, `ids`, `limit`          | Sent to the provider's search/index. `datetime` accepts an RFC3339 instant or interval with one open `..` endpoint.                                             |
| Search response `links[rel=next]`                                 | GET or POST pagination, including POST `body` and `merge`. Relative asset links use each Item's self URL, otherwise the page URL.                               |

No full STAC schema validation is performed. Other descriptive fields/extensions
are ignored. Query/filter/sort extensions, 3D bboxes, null footprints, collection
asset inheritance, cloud-native S3 reads, and core STAC 1.1 unified `bands` are
outside the documented field contract. An `s3:` asset reference can be consumed
with a signer that resolves it to a private HTTPS URL. Supply `raster:bands` or
`eo:bands` for multiband assets. An asset without either declares band 1 only.
STAC footprints describe discovery, not a pixel clipping mask.

## Python applications

Use the regular application constructor for SQL or STAC sources:

```python
import rcfp

app = rcfp.NativeApplication("docs/examples/data/lagoon-catalog.json")
status, content_type, body, cacheable, headers = app.request("GET", "/datasets")
assert status == 200
```

`NativeApplication`, `request`, `render_preview` and `rcfp.wsgi.Application`
accept the same source location or configuration file as `rcfp serve --source`.
Existing `database` argument names remain supported. Reuse `NativeApplication`
for multiple requests to retain workers, verified files and caches. WSGI and
Lambda use their existing `TC_DRIVER_PATH` source setting.

For programmatic selection, credential/signing callbacks or explicit render
grids, use `rcfp.stac.Selection` and `rcfp.stac.SearchClient`. Their methods and
options are listed in the [Python reference](../reference/python.md).
The runnable example writes metadata and a PNG for the included Item:

```sh
python docs/examples/stac_map.py --out build/stac-map
```

[STAC Catalog links](https://github.com/radiantearth/stac-spec/blob/v1.0.0/catalog-spec/catalog-spec.md),
[STAC Item fields](https://github.com/radiantearth/stac-spec/blob/v1.0.0/item-spec/item-spec.md),
[STAC API Item Search](https://github.com/radiantearth/stac-api-spec/blob/v1.0.0/item-search/README.md),
[Raster extension](https://github.com/stac-extensions/raster/tree/v1.1.0),
and [Projection extension](https://github.com/stac-extensions/projection/tree/v1.1.0)
define the source field conventions.
