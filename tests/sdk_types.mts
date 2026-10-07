import {
  Client,
  createState,
  restoreState,
  LatestRequest,
  type Render,
  type NativeRender,
  type DisplaySample,
} from "../js/sdk/index.js";
import {
  mapLibreSource,
  leafletLayer,
  openLayersSource,
} from "../js/sdk/integrations.js";
const client = new Client("https://example.test", { profile: "native" });
const sourceRevision: string | undefined = (await client.connect())
  .source_revision;
void sourceRevision;
const render: Render = {
  mode: "compute",
  keys: ["scene"],
  params: { expression: "v1-v2", v1: "nir", v2: "red", stretch_range: [-1, 1] },
};
const source = mapLibreSource(client, render);
const type: "raster" = source.type;
void type;
const native: NativeRender = {
  bindings: { v1: { asset: "image", band: 1 } },
  values: "scaled",
  channels: [{ expression: "v1", stretch: [0, 1] }],
  coverage: "any_valid",
  grid: {
    crs: "EPSG:3857",
    bounds: [0, 0, 1, 1],
    size: [256, 256],
    resampling: "bilinear",
  },
  encoding: "png",
};
await client.renderLayer("layer", native);
await client.batchMetadata([["scene", "red"]], {
  signal: new AbortController().signal,
});
const sample: DisplaySample = await client.inspect(render, {
  longitude: 12,
  latitude: 45,
  zoom: 12,
});
void sample;
const state = await createState({
  baseUrl: client.baseUrl,
  profile: client.profile,
  render,
  sourceRevision: "v1",
  view: { center: [12, 45], zoom: 12 },
});
await restoreState("#rcfp=state", state);
const result: string = await new LatestRequest().run(async () => "result");
void result;
leafletLayer(
  {
    tileLayer: (url: string) => ({
      url,
      on() {},
      getTileUrl() {
        return url;
      },
    }),
  },
  client,
  render,
);
// @ts-expect-error A Leaflet factory must return the tile URL/lifecycle methods.
leafletLayer({ tileLayer: (url: string) => ({ url }) }, client, render);
class XYZ {
  constructor(readonly options: { url: string }) {}
}
openLayersSource({ source: { XYZ } }, client, render);
const missingStretch: Render = {
  mode: "compute",
  keys: [],
  // @ts-expect-error Explicit compute stretch is required.
  params: { expression: "v1" },
};
void missingStretch;
// @ts-expect-error RGB selectors are required.
const missingBands: Render = { mode: "rgb", keys: [], params: {} };
void missingBands;
// @ts-expect-error Profiles are named policies, not arbitrary strings.
new Client("https://example.test", { profile: "latest" });
const secret: Render = {
  mode: "singleband",
  keys: [],
  // @ts-expect-error Authentication is not part of shared render state.
  params: { token: "secret" },
};
void secret;
const nativeTilesets = (await client.discoverTilesets()).tilesets;
const tileJSON = await client.tileJSON(nativeTilesets[0].keys, {
  colormap: "viridis",
});
const sourceValue: number | null = (
  await client.point(nativeTilesets[0].keys, { longitude: 12, latitude: 45 })
).raw;
void tileJSON;
void sourceValue;

const pythonTileJSON = await client.tileJSON(
  ["stress", "2021-06-29", "value"],
  {
    gain: 2,
    crop: "corn",
    irrigated: true,
    stretch_range: [-1, 3],
  },
);
const computeVersion: string | undefined = pythonTileJSON.rcfp.compute?.version;
void computeVersion;
