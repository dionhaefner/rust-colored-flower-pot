import {
  Client,
  VERSION,
  createState,
  encodeState,
  restoreState,
  type Render,
} from "@rcfp/sdk";
import {
  mapLibreSource,
  leafletLayer,
  openLayersSource,
} from "@rcfp/sdk/integrations";

function check(condition: boolean, message: string) {
  if (!condition) throw new Error(message);
}

const client = new Client("https://example.test", { profile: "native" });
const render: Render = {
  mode: "singleband",
  keys: ["scene / +%?#ü {x}", "red"],
  params: { stretch_range: [0, 3000] },
};
check(/^\d+\.\d+\.\d+/.test(VERSION), "Package version export is missing");
const template = client.tileTemplate(render);
check(
  mapLibreSource(client, render).tiles[0] === template,
  "MapLibre template differs",
);
check(
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
  ).url === template,
  "Leaflet template differs",
);
class XYZ {
  constructor(readonly options: { url: string }) {}
}
check(
  openLayersSource({ source: { XYZ } }, client, render).options.url ===
    template,
  "OpenLayers template differs",
);
check(
  template.includes("%7Bx%7D/red/{z}/{x}/{y}.png"),
  "Literal braces changed",
);
const state = await createState({
  baseUrl: client.baseUrl,
  profile: client.profile,
  sourceRevision: "public-v1",
  render,
  view: { center: [12, 45], zoom: 12 },
});
const restored = await restoreState(encodeState(state), {
  baseUrl: client.baseUrl,
  profile: client.profile,
  sourceRevision: "public-v1",
});
check(
  JSON.stringify(restored) === JSON.stringify(state),
  "Shared state changed",
);
console.log(
  `Installed SDK ${VERSION}: imports, adapters, encoding and revisions passed`,
);
