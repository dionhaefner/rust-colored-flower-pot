# JavaScript SDK and viewer

`@rcfp/sdk` is a dependency-free ES module package with TypeScript declarations.
Applications import `index.js` and `integrations.js` without depending on viewer code. RCFP embeds the same files in its native executable and wheels.
Start `rcfp serve` exposes them under `/explore/sdk/`. A standalone viewer started
with `rcfp connect HOST --port 5100` serves them at `/sdk/`. Both use a bundled
Natural Earth basemap by default.

## Install and discover

From a source checkout or source archive, install the local package:

```sh
npm install ./js/sdk
```

For a browser without a bundler, import from your viewer server, or copy the SDK
package into your application's static directory. Serve modules over HTTP(S).
Public `/sdk/` files permit cross-origin module imports.
RCFP artifacts contain the package. Independent SDK tags drive npm publication
after qualification, as described in [testing and releases](../how-to/release-sdk.md).
There is no requirement to install a UI framework or mapping
library to use the client.

```js
import { Client } from "/sdk/index.js";
const client = new Client("http://127.0.0.1:5000", { profile: "native" });
await client.connect();
const { keys, layers } = await client.discover();
const metadata = await client.metadata(layers[0].keys);
```

`connect()` verifies protocol `0.10.1` and the deployment's advertised profile.
A resolved catalog may advertise `source_revision`. Use it as the share state's
`sourceRevision`. STAC sources use normal dataset discovery and render parameters.
Select `native` or `terracotta` explicitly when creating the client. The server chooses its profile at startup and keeps it immutable.
This SDK requires an RCFP server advertising `compatibility_profile` at
`/__terracotta/identity`. It does not infer a profile from upstream Terracotta.

`discover()` paginates `/datasets` with the ordered `/keys` definitions and returns
`DatasetLayer` records (`id`, `keys`, `values`, `title`). Its default bound is 100
pages of 1000 rows. Exceeding the bound raises an error instead of returning an
incomplete discovery result. Large catalogs can use `datasets({ filters, page,
limit, orderBy, ascending, signal })` directly. Filters accept a string or a string
array. The server's list syntax cannot represent bracket/comma-containing array
values, so the SDK rejects those arrays. Scalar bracketed values retain the server's
list semantics. Use discovered full keys to select such datasets directly.

The final catalog key is the band selector only for RGB/compute routes. The SDK
does not impose that interpretation on registered native layers. Native catalogs
use `nativeLayers()`, `nativeLayer(id)`, and `renderLayer(id, spec, { signal })`. These expose the existing registered-layer contracts. `renderLayer(id)` uses the
server's default specification. Render requests cannot introduce raster paths.
The bundled viewer can display and inspect that default image. Its numeric
style/time controls and share links apply to key-based catalogs.

## Rendering and legends

```js
const render = {
  mode: "singleband",
  keys: ["lagoon", "red"],
  params: { stretch_range: [0, 3000], colormap: "viridis" },
};
const tiles = client.tileTemplate(render); // contains {z}/{x}/{y}
const tile = await client.tile(render, { z: 12, x: 2188, y: 1466 });
const preview = await client.preview(render);
const legend = await client.legend({
  colormap: "viridis",
  stretch_range: [0, 3000],
});
```

`tileUrl`, `tileTemplate`, and `previewUrl` encode each key component and JSON
query parameter. Unicode, spaces, slashes, plus, percent, ampersand and question
marks retain their literal meaning. Dot-segment and empty keys are rejected to
avoid browser URL normalization. RGB uses `mode: 'rgb'`, keys excluding the band,
and `r/g/b` plus `r_range/g_range/b_range`. Expressions use `mode: 'compute'`,
keys excluding the band, and `expression`, `v1`–`v5`, and a numeric
`stretch_range`. See the adjacent TypeScript declarations for the full types.
Native source-band and WebP options are rejected by a compatibility-profile
client. Source-band indexes work on tiles only. Both render types use the same
public metadata and image routes as singleband.

Legends contain the server's numeric samples and RGBA colors from `/colormap`.
They describe a numeric stretch/colormap, rather than interpreting a color
transform or an RGB composite as a quantitative scale. The viewer displays
numeric singleband/expression legends and omits an RGB scale.

## Map integrations and independent applications

`integrations.js` accepts mapping libraries supplied by the application. It
constructs their raster sources through `client.tileTemplate()` and enables
anonymous CORS for pixel-readable tiles. Map integrations require square tiles.

```js
import {
  leafletLayer,
  mapLibreSource,
  openLayersSource,
} from "/sdk/integrations.js";
const leafletTiles = leafletLayer(L, client, render);
const mapLibreRaster = mapLibreSource(client, render);
const openLayersTiles = openLayersSource(ol, client, render);
```

`leafletLayer` shares a three-request limit across layers and discards pending
tiles when removed. It uses the client's fetch transport and retries HTTP 503
responses at most twice, after 150 and 300 ms. Permanent errors remain tile
errors. This paces RGB views at the server's default raster capacity. Unrelated
clients or smaller deployment limits can still exhaust it.

Three complete, independent applications are served at
`/examples/leaflet.html`, `/examples/maplibre.html`, and
`/examples/openlayers.html`. Their source is in `src/assets/client/examples`.
In native mode each imports the public SDK, discovers tilesets and uses their
TileJSON manifests to frame and display the raster. The named compatibility
profile uses keyed discovery and metadata. They share no viewer
internals or URL-building code. Leaflet 1.9.4, MapLibre GL JS 5.7.3 and OpenLayers
10.6.1 are bundled locally with acquisition checksums and licenses. MapLibre
requires WebGL. The viewer's Leaflet map uses DOM tiles. Raster backgrounds are empty
and all examples work without tokens or outbound requests. Their library usage
follows the [Leaflet reference](https://leafletjs.com/reference),
[MapLibre raster source](https://maplibre.org/maplibre-gl-js/docs/examples/add-a-raster-tile-source/),
and [OpenLayers XYZ example](https://openlayers.org/en/latest/examples/xyz.html).

An application may add a provider basemap separately. Keep its authentication in
application configuration, obey its provider policy, and qualify actual network,
CORS, token restrictions and attribution before deployment. External basemap
integration is separate from the locally verified offline workflows.

Native `discoverTilesets`, `tileJSON` and `point` expose
[map discovery and source values](native-services.md). The additional
`leafletTileJSON`, `mapLibreTileJSONSource` and `openLayersTileJSONSource`
integrations consume the same TileJSON manifest. The original rendering adapters
remain available in both profiles.

## Time and point inspection

Choose a catalog key explicitly as the time dimension. `temporalValues(layers,
selected, timeKey)` lists available values while holding every other key fixed. `selectTime(layers, selected, timeKey, value)` selects that full dataset. String
values sort lexicographically. Use normalized ISO timestamps for chronological order. The browser does not infer acquisition dates, interpolate,
choose the closest time, or change server observation ranking. Native-layer
selection is currently stored on the server and has no browser time-filter route.

`inspect(render, { longitude, latitude, zoom }, { signal })` fetches and decodes
the same displayed XYZ tile. Its result has `kind: 'display'`, `rgba`, `nodata`,
`pixel` and `size`. `inspectImage(blob, [u, v])` samples normalized image
coordinates. Transparency is reported as no displayed data. In the viewer, turn
on **Inspect** and click the map. With keyboard focus on the map, press Enter to
inspect its center.

These are display values after sampling, expressions, stretches and encoding.
RGBA cannot recover accurate source samples. Image decoding uses the browser's
decoded RGBA values, including its alpha/color conversion.

For native keyed layers, `point(keys, { longitude, latitude }, { signal })`
requests a source sample from the server. The viewer shows this value when
available for single-band rendering and labels the display RGBA separately.
RGB composites, browser expressions, and the Terracotta profile use display
inspection. See the [native point contract](native-services.md#inspect-source-values) for source
sampling semantics and supported layers.

## Abort, errors and CORS

Every asynchronous request accepts `{ signal }`. A caller-owned `fetch` function
can supply authentication or deployment transport configuration. The default
transport uses `credentials: 'omit'` and keeps authentication out of URLs and state.
Fetch cancellation rejects with `AbortError`. `RcfpError` distinguishes `http`
(with status and parsed JSON detail), `network` (including browser CORS denial),
`profile`, `contract` and `state` failures. No retry is implicit.

```js
import { LatestRequest } from "/sdk/index.js";
const current = new LatestRequest();
const metadata = await current.run((signal) =>
  client.metadata(keys, { signal }),
);
// Starting another run aborts this run. Even a late non-abortable result is rejected.
```

Use one `LatestRequest` per replaceable workflow. The viewer cancels old metadata,
legends and inspection and clears old raster sources on selection/style changes.
Discard `AbortError` in your UI. Report other failures and allow another request.
Mapping libraries own their tile-loading cancellation separately. Removing the
old layer prevents late images from painting the replacement view. Aborting a
browser fetch leaves active GDAL work accounted for until it finishes.

A different port is a different origin. Permit the application origin through
`ALLOWED_ORIGINS_METADATA` and `ALLOWED_ORIGINS_TILES`. Identity uses the metadata policy. JSON POST metadata/layer rendering triggers a preflight. Custom
Authorization headers trigger one too. The default SDK transport does not use
credentialed cookies. Deployments needing them must supply their own transport and a suitable proxy/CORS policy. A CORS error may prevent JavaScript from seeing
the HTTP status or JSON detail. Test both successful calls and denied origins in
a real browser, rather than relying on command-line HTTP success.

## Shared state and revisions

```js
import { createState, encodeState, restoreState } from "/sdk/index.js";
const state = await createState({
  baseUrl: client.baseUrl,
  profile: client.profile,
  render,
  sourceRevision: "public-catalog-v1",
  view: { center: [12.36, 45.46], zoom: 12 },
});
const fragment = encodeState(state);
const restored = await restoreState(fragment, {
  baseUrl: client.baseUrl,
  profile: client.profile,
  sourceRevision: "public-catalog-v1",
});
```

State version 1 contains the service URL, profile, public source revision,
complete rendering request, SHA-256 render revision, optional time key and map
view. The render revision verifies the canonical rendering specification.
Restore rejects a changed service, profile, source revision or render digest.
Fragments support Unicode and have a size limit. Unknown fields and authentication parameters are rejected. HTTPS or localhost is required for browser Web Crypto.
The digest checks consistency. It provides neither a signature nor access control.

The server currently has no key-catalog revision endpoint. The operator must
supply the actual public catalog/source revision to the application and host
immutable or revisioned raster URLs. A state revision does not invalidate GDAL
caches or prove that mutable URLs still contain the same bytes. Start the viewer
with `rcfp connect HOST --source-revision public-catalog-v1` to enable that check
on reload. Its default `unversioned` revision is explicitly mutable and the viewer
warns that future samples may differ. The client cannot independently verify
that the operator has updated a revision when changing data.

State intentionally contains no catalog metadata, raster locations,
Authorization headers, tokens or cookies. Use only public service URLs, keys and
revision labels. An authentication token embedded in a service path or a dataset
name is still a secret. Keep tokens out of both. Shared state is visible to recipients
and browser history, not an authorization mechanism.

## Versions, support and accessibility

The SDK package version is `0.1.0` and is independent of the Python package's
Hatch VCS distribution version and HTTP protocol version. Pin the SDK version.
Before 1.0, incompatible SDK/type/state changes require a minor version increase. Compatible fixes use patch versions. A new state schema must have a new explicit
version and reject unsupported versions. Release validation checks that the
exported constant, package metadata, declarations and embedded files agree.
The [release guide](../how-to/release-sdk.md) defines the Node/package/browser gates,
release tags and registry setup. The public surface is the exported SDK modules and types. DOM structure and
viewer internals are not an extension API.

The viewer uses standard labeled controls, keyboard-operable buttons/selects,
visible focus, live status/error/inspection text, textual numeric legends,
expandable metadata and a narrow-screen layout. Leaflet provides keyboard map
pan/zoom, and inspection is also available without a pointer. Application authors
must retain equivalent alternatives when using custom controls. Automated
Chromium tests exercise keyboard controls and mobile layout. Formal WCAG and screen-reader qualification remain separate. The SDK requires ES modules, fetch, AbortController, Web Crypto and createImageBitmap. Chromium is the executed browser gate. Firefox and Safari remain unqualified.

Python-hosted compute layers use the same native discovery and TileJSON methods.
`tileJSON(keys, { gain: 2, crop: "wheat", colormap: "viridis" })` passes declared
model parameters alongside display settings. Computed descriptors expose
`rcfp.compute`. Source inspection, output percentiles and source revision fields
can be absent for these layers. See [Python compute layers](../how-to/python-compute.md).
