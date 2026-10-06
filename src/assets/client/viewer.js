import {
  Client,
  LatestRequest,
  createState,
  encodeState,
  restoreState,
  temporalValues,
  selectTime,
  inspectImage,
} from "/sdk/index.js";
import { leafletLayer } from "/sdk/integrations.js";
const $ = (id) => document.getElementById(id);
const client = new Client(document.body.dataset.service, {
  profile: document.body.dataset.profile,
});
const map = L.map("map").setView([0, 0], 2);
const rendering = new LatestRequest(),
  inspection = new LatestRequest(),
  restoring = new LatestRequest();
let discovery, selected, active, tiles, nativeIds, nativeBlob, nativeUrl;
let revision = 0;
const status = (message) => {
  $("status").textContent = message;
};
function error(error) {
  if (error.name !== "AbortError")
    status(
      `${error.message}${error.detail ? ": " + JSON.stringify(error.detail) : ""}`,
    );
}
function options(select, values, chosen) {
  select.replaceChildren(
    ...values.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      return option;
    }),
  );
  if (values.includes(chosen)) select.value = chosen;
}
function clear() {
  revision++;
  rendering.cancel();
  inspection.cancel();
  restoring.cancel();
  nativeBlob = undefined;
  $("inspection").textContent =
    "Inspection reports display RGBA; source values are unavailable.";
  if (tiles) {
    tiles.remove();
    tiles = undefined;
  }
  if (nativeUrl) {
    URL.revokeObjectURL(nativeUrl);
    nativeUrl = undefined;
  }
  $("native-image").hidden = true;
  active = undefined;
}
function timeOptions() {
  const key = $("time-key").value;
  $("time").disabled = !key;
  options(
    $("time"),
    key ? temporalValues(discovery.layers, selected, key) : [],
    key ? selected.values[key] : undefined,
  );
}
function bands() {
  const prefix = selected.keys.slice(0, -1);
  const choices = [
    ...new Set(
      discovery.layers
        .filter((layer) => prefix.every((key, i) => layer.keys[i] === key))
        .map((layer) => layer.keys.at(-1)),
    ),
  ];
  for (const channel of ["r", "g", "b", "v1", "v2"])
    options(
      $(channel),
      choices,
      $(channel).value ||
        { r: "red", g: "green", b: "blue", v1: "nir", v2: "red" }[channel],
    );
}
function showMode() {
  const mode = $("mode").value;
  $("single-controls").hidden = mode !== "singleband";
  $("rgb-controls").hidden = mode !== "rgb";
  $("expression-controls").hidden = mode !== "compute";
}
function specification() {
  const mode = $("mode").value;
  const range = [Number($("minimum").value), Number($("maximum").value)];
  if (!range.every(Number.isFinite) || range[0] > range[1])
    throw new Error("Enter a finite increasing stretch");
  const params =
    mode === "rgb"
      ? {
          r: $("r").value,
          g: $("g").value,
          b: $("b").value,
          r_range: range,
          g_range: range,
          b_range: range,
        }
      : {
          stretch_range: range,
          ...(mode === "compute"
            ? {
                expression: $("expression").value,
                v1: $("v1").value,
                v2: $("v2").value,
              }
            : {}),
          ...($("colormap").value ? { colormap: $("colormap").value } : {}),
        };
  return {
    mode,
    keys: mode === "singleband" ? selected.keys : selected.keys.slice(0, -1),
    params,
  };
}
function showLegend(result) {
  $("legend").replaceChildren(
    ...result.colormap.map(({ value, rgba }) => {
      const cell = document.createElement("span");
      cell.className = "swatch";
      cell.style.background = `linear-gradient(to bottom, rgba(${rgba[0]},${rgba[1]},${rgba[2]},${rgba[3] / 255}) 1.5rem, transparent 1.5rem)`;
      cell.textContent = Number(value.toPrecision(3)).toString();
      return cell;
    }),
  );
}
async function apply(fit = false, restored) {
  clear();
  status("Loading rendering…");
  try {
    const render = restored ?? specification();
    const data = await rendering.run(async (signal) => {
      const metadataKeys =
        render.mode === "singleband"
          ? render.keys
          : [
              ...render.keys,
              render.mode === "rgb" ? render.params.r : render.params.v1,
            ];
      const [metadata, legend] = await Promise.all([
        client.metadata(metadataKeys, { signal }),
        render.mode === "rgb"
          ? Promise.resolve({ colormap: [] })
          : client.legend(
              {
                colormap: render.params.colormap,
                stretch_range: render.params.stretch_range,
              },
              { signal },
            ),
      ]);
      return { metadata, legend, render };
    });
    $("metadata").textContent = JSON.stringify(data.metadata, null, 2);
    showLegend(data.legend);
    const version = revision;
    tiles = leafletLayer(L, client, data.render).addTo(map);
    tiles.on("tileerror", () => {
      if (version === revision)
        status("Tile failed; check service availability and tile CORS.");
    });
    active = data.render;
    if (fit) {
      const [west, south, east, north] = data.metadata.bounds;
      map.fitBounds(
        [
          [south, west],
          [north, east],
        ],
        { maxZoom: 16 },
      );
    }
    status(client.profile + ": " + selected.title);
  } catch (failure) {
    error(failure);
  }
}
async function applyNative() {
  clear();
  status("Loading native layer…");
  try {
    const result = await rendering.run(async (signal) => {
      const definition = await client.nativeLayer($("dataset").value, {
        signal,
      });
      const blob = await client.renderLayer($("dataset").value, {}, { signal });
      return { definition, blob };
    });
    nativeBlob = result.blob;
    nativeUrl = URL.createObjectURL(nativeBlob);
    $("native-image").src = nativeUrl;
    $("native-image").hidden = false;
    $("metadata").textContent = JSON.stringify(result.definition, null, 2);
    status(
      "Native default rendering. This layer's selection and grid are configured by its server.",
    );
  } catch (failure) {
    error(failure);
  }
}
function choose(fit = true) {
  if (nativeIds) return applyNative();
  selected = discovery.layers.find((layer) => layer.id === $("dataset").value);
  bands();
  timeOptions();
  return apply(fit);
}
async function inspect(point) {
  try {
    const render = active;
    if (!render && !nativeBlob)
      throw new Error("Load a rendering before inspection");
    const sample = await inspection.run((signal) =>
      nativeIds
        ? inspectImage(nativeBlob, [0.5, 0.5], { signal })
        : client.inspect(
            render,
            {
              longitude: ((((point.lng + 180) % 360) + 360) % 360) - 180,
              latitude: point.lat,
              zoom: Math.floor(map.getZoom()),
            },
            { signal },
          ),
    );
    $("inspection").textContent = sample.nodata
      ? "No displayed data at this point (transparent pixel)."
      : `Display RGBA: ${sample.rgba.join(", ")}. Source values are unavailable.`;
  } catch (failure) {
    error(failure);
  }
}
map.on("click", (event) => inspect(event.latlng));
map.on("movestart", () => inspection.cancel());
$("inspect").onclick = () => inspect(map.getCenter());
$("native-image").onclick = async (event) => {
  const rect = event.target.getBoundingClientRect();
  try {
    const sample = await inspection.run((signal) =>
      inspectImage(
        nativeBlob,
        [
          (event.clientX - rect.left) / rect.width,
          (event.clientY - rect.top) / rect.height,
        ],
        { signal },
      ),
    );
    $("inspection").textContent = `Display RGBA: ${sample.rgba.join(", ")}.`;
  } catch (failure) {
    error(failure);
  }
};
$("dataset").onchange = () => choose();
$("mode").onchange = showMode;
$("time-key").onchange = timeOptions;
$("time").onchange = () => {
  selected = selectTime(
    discovery.layers,
    selected,
    $("time-key").value,
    $("time").value,
  );
  $("dataset").value = selected.id;
  bands();
  apply();
};
$("render-form").onsubmit = (event) => {
  event.preventDefault();
  apply();
};
$("share").onclick = async () => {
  try {
    if (!active) throw new Error("Load a catalog rendering before sharing");
    const center = map.getCenter();
    const sharedRevision = revision;
    const state = await createState({
      baseUrl: client.baseUrl,
      profile: client.profile,
      render: active,
      sourceRevision: $("source-revision").value,
      view: {
        center: [((((center.lng + 180) % 360) + 360) % 360) - 180, center.lat],
        zoom: map.getZoom(),
      },
      timeKey: $("time-key").value || null,
    });
    if (sharedRevision !== revision)
      throw new DOMException("View was replaced", "AbortError");
    const fragment = encodeState(state);
    history.replaceState(null, "", fragment);
    $("share-link").value = location.href;
    status(
      state.sourceRevision === "unversioned"
        ? "Link created. The service is mutable; identical future samples are not guaranteed."
        : "Share link created for the configured public source revision.",
    );
  } catch (failure) {
    error(failure);
  }
};
async function restore(fragment) {
  try {
    if (fragment === undefined)
      fragment = new URL($("share-link").value || location.href).hash;
    const state = await restoring.run(() =>
      restoreState(fragment, {
        baseUrl: client.baseUrl,
        profile: client.profile,
        sourceRevision: $("source-revision").value,
      }),
    );
    const keys =
      state.render.mode === "singleband"
        ? state.render.keys
        : [
            ...state.render.keys,
            state.render.mode === "rgb"
              ? state.render.params.r
              : state.render.params.v1,
          ];
    selected = discovery.layers.find(
      (layer) => JSON.stringify(layer.keys) === JSON.stringify(keys),
    );
    if (!selected) throw new Error("The shared dataset is no longer available");
    $("dataset").value = selected.id;
    $("time-key").value = state.timeKey || "";
    bands();
    timeOptions();
    $("mode").value = state.render.mode;
    showMode();
    const params = state.render.params,
      range = params.stretch_range || params.r_range || [0, 3000];
    $("minimum").value = range[0];
    $("maximum").value = range[1];
    $("colormap").value = params.colormap || "";
    for (const key of ["r", "g", "b", "v1", "v2", "expression"])
      if (params[key] !== undefined) $(key).value = params[key];
    map.setView([state.view.center[1], state.view.center[0]], state.view.zoom);
    await apply(false, state.render);
  } catch (failure) {
    error(failure);
  }
}
$("restore").onclick = () => restore();
window.addEventListener("hashchange", () => restore(location.hash));
window.addEventListener("pagehide", () => {
  rendering.cancel();
  inspection.cancel();
  restoring.cancel();
  if (nativeUrl) URL.revokeObjectURL(nativeUrl);
});
try {
  const identity = await client.connect();
  if (identity.source_revision)
    $("source-revision").value = identity.source_revision;
  try {
    discovery = await client.discover();
  } catch (failure) {
    if (
      failure.kind !== "http" ||
      failure.status !== 400 ||
      client.profile !== "native"
    )
      throw failure;
    nativeIds = (await client.nativeLayers()).layers;
  }
  if (nativeIds) {
    options($("dataset"), nativeIds);
    for (const id of [
      "render-form",
      "time-key",
      "time",
      "map",
      "legend",
      "share",
      "restore",
    ])
      $(id).hidden = true;
    for (const id of ["time-key", "time", "source-revision", "share-link"]) {
      $(id).hidden = true;
      document.querySelector(`label[for="${id}"]`).hidden = true;
    }
    document.querySelector('nav[aria-label="SDK examples"]').hidden = true;
    document.querySelector("section > p").textContent =
      "Click the image to inspect display pixels, or inspect its center with the button.";
    if (!nativeIds.length) status("The catalog has no layers.");
    else await applyNative();
  } else {
    $("dataset").replaceChildren(
      ...discovery.layers.map((layer) => {
        const option = document.createElement("option");
        option.value = layer.id;
        option.textContent = layer.title;
        return option;
      }),
    );
    for (const key of discovery.keys.slice(0, -1)) {
      const option = document.createElement("option");
      option.value = key.key;
      option.textContent = key.key;
      $("time-key").append(option);
    }
    if (!discovery.layers.length) status("The catalog has no datasets.");
    else if (location.hash) await restore(location.hash);
    else await choose();
  }
} catch (failure) {
  error(failure);
}
