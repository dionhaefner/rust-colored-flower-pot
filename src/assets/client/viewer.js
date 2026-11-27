import {
  Client,
  LatestRequest,
  createState,
  encodeState,
  restoreState,
  temporalValues,
  selectTime,
  inspectImage,
} from "./sdk/index.js";
import { leafletLayer } from "./sdk/integrations.js";
import { connectService, serviceUrl } from "./viewer-connection.js";

const $ = (id) => document.getElementById(id);
const loopback = (hostname) =>
  /^(?:(?:.+\.)?localhost\.?|127(?:\.\d{1,3}){3}|\[::1\])$/i.test(hostname);
const hosted = !document.body.dataset.service;
const demo = document.body.dataset.demo === "true";
const pageUrl = new URL(location.href);
let client, serverUrl, canShare;
const map = L.map("map", { zoomControl: false, zoomSnap: 0.25 }).setView(
  [0, 0],
  2,
);
L.control.zoom({ position: "topright" }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
const rendering = new LatestRequest(),
  inspection = new LatestRequest(),
  restoring = new LatestRequest(),
  channelRequest = new LatestRequest();
const mobile = matchMedia("(max-width: 700px)");
const channelKeys = ["r", "g", "b"];
let discovery,
  selected,
  metadata,
  layerMetadata,
  appliedExpression,
  active,
  tiles,
  pendingTiles,
  nativeIds,
  nativeBlob,
  nativeUrl,
  rasterCredit,
  marker;
const channelMetadata = new Map();
let sourceRevision =
    pageUrl.searchParams.get("source-revision") ||
    document.body.dataset.sourceRevision,
  generation = 0,
  timer,
  noticeTimer,
  shown = 30,
  loadingSelection = false,
  loadingChannels = false,
  inspecting = false;
const number = (value) =>
  Number.isFinite(value)
    ? new Intl.NumberFormat(undefined, { maximumSignificantDigits: 7 }).format(
        value,
      )
    : "—";
const coordinate = (point) => ({
  longitude: ((((point.lng + 180) % 360) + 360) % 360) - 180,
  latitude: point.lat,
});

function status(
  message,
  { error = false, loading = false, transient = false } = {},
) {
  clearTimeout(noticeTimer);
  $("status").textContent = message;
  $("notice").hidden = false;
  $("notice").dataset.error = error;
  $("notice").dataset.loading = loading;
  $("retry").hidden = !error;
  if (transient)
    noticeTimer = setTimeout(() => {
      $("notice").hidden = true;
    }, 3500);
}
function fail(error) {
  if (error.name === "AbortError") return;
  const message =
    error.detail?.message ||
    error.message ||
    "The request could not be completed.";
  status(`Could not load this view. ${message}`, { error: true });
  $("workspace").setAttribute("aria-busy", "false");
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
function panel(open) {
  $("panel").dataset.open = open;
  document.body.dataset.panelOpen = mobile.matches && open;
  $("panel-toggle").setAttribute("aria-expanded", String(open));
  $("panel-content").inert = mobile.matches && !open;
}
function tab(name, open = true) {
  for (const key of ["layers", "style", "details"]) {
    $("tab-" + key).setAttribute("aria-selected", String(name === key));
    $("tab-" + key).tabIndex = name === key ? 0 : -1;
    $("pane-" + key).hidden = name !== key;
  }
  $("panel-content").scrollTop = 0;
  if (mobile.matches && open) panel(true);
}
for (const name of ["layers", "style", "details"]) {
  $("tab-" + name).onclick = () => tab(name);
  $("tab-" + name).onkeydown = (event) => {
    const tabs = ["layers", "style", "details"],
      index = tabs.indexOf(name);
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? 2
          : (index + (event.key === "ArrowRight" ? 1 : 2)) % 3;
    tab(tabs[next]);
    $("tab-" + tabs[next]).focus();
  };
}
$("panel-toggle").onclick = () => panel($("panel").dataset.open !== "true");
mobile.addEventListener("change", () => {
  panel(false);
  map.invalidateSize();
  fit();
});
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    mobile.matches &&
    !document.querySelector("dialog[open]")
  ) {
    panel(false);
    $("panel-toggle").focus();
  }
});

function layers() {
  return nativeIds
    ? nativeIds.map((id) => ({ id, title: id, keys: [id] }))
    : discovery?.layers || [];
}
function listLayers() {
  const focused = document.activeElement?.dataset.layerId;
  const terms = $("layer-search")
    .value.trim()
    .toLocaleLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  const scene = demo ? $("demo-scene").value : "";
  const found = layers().filter(
    (layer) =>
      (!scene || layer.keys[0] === scene) &&
      terms.every((term) => layer.title.toLocaleLowerCase().includes(term)),
  );
  $("search-summary").textContent = terms.length
    ? `${found.length} matching ${found.length === 1 ? "layer" : "layers"}`
    : "";
  $("no-results").hidden = found.length > 0 || !terms.length;
  $("more-layers").hidden = found.length <= shown;
  $("layer-list").replaceChildren(
    ...found.slice(0, shown).map((layer) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.layerId = layer.id;
      button.className = "layer-row";
      button.setAttribute("aria-label", layer.title);
      const displayed =
        layer.id === selected?.id &&
        (nativeIds || $("mode").value === "singleband");
      button.setAttribute("aria-pressed", String(!!displayed));
      const glyph = document.createElement("span");
      glyph.className = "layer-glyph";
      glyph.setAttribute("aria-hidden", "true");
      glyph.innerHTML = '<svg><use href="#i-layers" /></svg>';
      const label = document.createElement("span");
      label.className = "layer-label";
      const name = document.createElement("span");
      name.className = "layer-name";
      name.textContent = layer.keys.at(-1);
      const context = document.createElement("span");
      context.className = "layer-context";
      context.textContent = scene ? "" : layer.keys.slice(0, -1).join(" / ");
      context.hidden = !context.textContent;
      label.append(name, context);
      button.append(glyph, label);
      if (displayed) {
        const check = document.createElement("span");
        check.className = "layer-check";
        check.textContent = "✓";
        check.setAttribute("aria-hidden", "true");
        button.append(check);
      }
      button.onclick = () => {
        choose(layer);
        if (mobile.matches) panel(false);
      };
      return button;
    }),
  );
  if (focused)
    [...$("layer-list").children]
      .find((row) => row.dataset.layerId === focused)
      ?.focus({ preventScroll: true });
}
$("layer-search").oninput = () => {
  shown = 30;
  listLayers();
};
$("more-layers").onclick = () => {
  shown += 30;
  listLayers();
};
function selectDemoScene(scene) {
  const choices = layers().filter((layer) => layer.keys[0] === scene);
  const red = choices.find((layer) => layer.keys.at(-1) === "red");
  const rgb = ["red", "green", "blue"].every((band) =>
    choices.some((layer) => layer.keys.at(-1) === band),
  );
  $("layer-search").value = "";
  $("colormap").value = choices[0].keys.at(-1) === "elevation" ? "terrain" : "";
  return choose(red || choices[0], { mode: rgb ? "rgb" : "singleband" });
}
$("demo-scene").onchange = () => selectDemoScene($("demo-scene").value);
function bandChoices() {
  const prefix = selected.keys.slice(0, -1);
  return [
    ...new Set(
      discovery.layers
        .filter((layer) => prefix.every((key, i) => layer.keys[i] === key))
        .map((layer) => layer.keys.at(-1)),
    ),
  ];
}
function bands() {
  const choices = bandChoices();
  const defaults = { r: "red", g: "green", b: "blue", v1: "nir", v2: "red" };
  for (const name of Object.keys(defaults))
    options(
      $(name),
      choices,
      choices.includes($(name).value) ? $(name).value : defaults[name],
    );
  $("ndvi").hidden = !(choices.includes("red") && choices.includes("nir"));
  $("mode").querySelector('[value="rgb"]').disabled = choices.length < 2;
  $("mode").querySelector('[value="compute"]').disabled = choices.length < 2;
  if (choices.length < 2) $("mode").value = "singleband";
  const candidates = discovery.keys
    .slice(0, -1)
    .filter(
      ({ key }) => temporalValues(discovery.layers, selected, key).length > 1,
    );
  const chosen = $("time-key").value;
  options($("time-key"), ["", ...candidates.map(({ key }) => key)], chosen);
  $("time-key").options[0].textContent = "Choose a key…";
  $("time-controls").hidden = !candidates.length;
  timeOptions();
  showMode();
}
function timeOptions() {
  const key = $("time-key").value,
    values = key ? temporalValues(discovery.layers, selected, key) : [];
  $("time-row").hidden = !key;
  options($("time"), values, selected.values?.[key]);
  const index = values.indexOf($("time").value);
  $("previous-time").disabled = index <= 0;
  $("next-time").disabled = index < 0 || index >= values.length - 1;
}
function showMode() {
  const mode = $("mode").value;
  const awaitingExpression = mode === "compute" && !appliedExpression;
  $("rgb-bands").hidden = mode !== "rgb";
  $("expression-controls").hidden = mode !== "compute";
  $("render-form").hidden = awaitingExpression;
  $("style-empty").hidden = !awaitingExpression;
  $("reset-style").disabled = awaitingExpression;
  $("single-controls").hidden = mode === "rgb";
  $("scalar-contrast").hidden = mode === "rgb";
  $("rgb-contrast").hidden = mode !== "rgb";
  $("rgb-correction").hidden = mode !== "rgb";
  $("auto-contrast").hidden = mode === "compute";
  listLayers();
}
function rangeIds(key) {
  return key === "single"
    ? ["minimum", "maximum"]
    : [key + "-min", key + "-max"];
}
function rangeValue(key) {
  return rangeIds(key).map((id) => Number($(id).value));
}
function autoRange(meta) {
  const p = meta?.percentiles;
  const values = p?.length >= 98 ? [p[1], p[97]] : meta?.range;
  return values?.every(Number.isFinite) ? values : [0, 1];
}
function setRange(key, values, domain = values) {
  const low = Math.min(domain[0], values[0]),
    high = Math.max(domain[1], values[1]);
  const pad = high === low ? Math.max(1, Math.abs(low) * 0.01) : 0;
  const ids = rangeIds(key);
  values.forEach((value, i) => {
    $(ids[i]).value = value;
    const slider = $(key + (i ? "-high" : "-low"));
    slider.min = low - pad;
    slider.max = high + pad;
    slider.step = (high - low + 2 * pad) / 1000;
    slider.value = value;
  });
}
function summary(meta) {
  metadata = meta;
  $("metadata").textContent = JSON.stringify(meta, null, 2);
  const rows = [];
  if (meta.range)
    rows.push(["Value range", meta.range.map(number).join(" to ")]);
  if (meta.mean !== undefined) rows.push(["Mean", number(meta.mean)]);
  if (meta.stdev !== undefined)
    rows.push(["Standard deviation", number(meta.stdev)]);
  if (meta.valid_percentage !== undefined)
    rows.push(["Valid pixels", `${number(meta.valid_percentage)}%`]);
  if (meta.bounds)
    rows.push([
      "Bounds (W, S, E, N)",
      meta.bounds.map((v) => Number(v.toFixed(4))).join(", "),
    ]);
  $("metadata-summary").replaceChildren(
    ...rows.flatMap(([key, value]) => {
      const dt = document.createElement("dt"),
        dd = document.createElement("dd");
      dt.textContent = key;
      dd.textContent = value;
      return [dt, dd];
    }),
  );
}
function updateAttribution(value) {
  if (rasterCredit) map.attributionControl.removeAttribution(rasterCredit);
  const credit = document.createElement("span");
  credit.textContent = typeof value === "string" ? value : "";
  rasterCredit = credit.textContent ? credit.outerHTML : undefined;
  if (rasterCredit) map.attributionControl.addAttribution(rasterCredit);
}
function specification() {
  const mode = $("mode").value;
  const range = (key) => {
    const values = rangeValue(key);
    if (
      rangeIds(key).some((id) => $(id).value.trim() === "") ||
      !values.every(Number.isFinite) ||
      values[0] > values[1]
    )
      throw new Error(
        "The minimum must be a number no greater than the maximum.",
      );
    return values;
  };
  return {
    mode,
    keys: mode === "singleband" ? selected.keys : selected.keys.slice(0, -1),
    params:
      mode === "rgb"
        ? {
            r: $("r").value,
            g: $("g").value,
            b: $("b").value,
            r_range: range("r"),
            g_range: range("g"),
            b_range: range("b"),
            ...($("color-correction").value
              ? { color_transform: $("color-correction").value }
              : {}),
          }
        : {
            stretch_range: range("single"),
            ...($("colormap").value ? { colormap: $("colormap").value } : {}),
            ...(mode === "compute" ? appliedExpression : {}),
          },
  };
}
function metadataKeys(render) {
  return render.mode === "singleband"
    ? render.keys
    : [
        ...render.keys,
        render.mode === "rgb" ? render.params.r : render.params.v1,
      ];
}
function showLegend(result, render) {
  $("legend").replaceChildren();
  $("legend").hidden = !result.colormap.length;
  if (!result.colormap.length) return;
  const title = document.createElement("div");
  title.className = "legend-title";
  title.textContent =
    render.mode === "compute" ? "Expression value" : "Raster value";
  const bar = document.createElement("div");
  bar.className = "legend-bar";
  bar.append(
    ...result.colormap.map(({ rgba }) => {
      const cell = document.createElement("span");
      cell.className = "swatch";
      cell.style.background = `rgba(${rgba[0]},${rgba[1]},${rgba[2]},${rgba[3] / 255})`;
      return cell;
    }),
  );
  const labels = document.createElement("div");
  labels.className = "legend-labels";
  for (const value of render.params.stretch_range) {
    const label = document.createElement("span");
    label.textContent = number(value);
    labels.append(label);
  }
  $("legend").append(title, bar, labels);
}
function fit() {
  if (!metadata?.bounds) return;
  const [west, south, east, north] = metadata.bounds;
  map.fitBounds(
    [
      [south, west],
      [north, east],
    ],
    {
      maxZoom: 17,
      animate: false,
      paddingTopLeft: mobile.matches ? [24, 40] : [392, 40],
      paddingBottomRight: mobile.matches ? [54, 200] : [100, 80],
    },
  );
}
async function apply({ fitView = false, restored } = {}) {
  if (
    !selected ||
    nativeIds ||
    loadingSelection ||
    loadingChannels ||
    ($("mode").value === "compute" && !appliedExpression)
  )
    return;
  clearTimeout(timer);
  clearInspection();
  const version = ++generation;
  status("Updating map…", { loading: true });
  $("workspace").setAttribute("aria-busy", "true");
  try {
    const render = restored || specification();
    const keys = metadataKeys(render);
    const result = await rendering.run(async (signal) => {
      const [meta, legend] = await Promise.all([
        client.metadata(keys, { signal }),
        render.mode === "rgb"
          ? { colormap: [] }
          : client.legend(
              {
                colormap: render.params.colormap,
                stretch_range: render.params.stretch_range,
              },
              { signal },
            ),
      ]);
      return { meta, legend };
    });
    if (version !== generation) return;
    if (JSON.stringify(keys) === selected.id) layerMetadata = result.meta;
    summary(result.meta);
    updateAttribution(result.meta.metadata?.attribution);
    showLegend(result.legend, render);
    active = render;
    const title =
      render.mode === "singleband"
        ? ""
        : `${render.keys.join(" / ")} / ${render.mode === "rgb" ? "RGB" : "Expression"}`;
    $("header-layer").textContent = title;
    $("image-url").href = client.previewUrl(render);
    $("image-url").hidden = false;
    if (pendingTiles) {
      map.removeLayer(pendingTiles);
      pendingTiles = undefined;
    }
    const next = leafletLayer(L, client, render);
    next.setOpacity(Number($("opacity").value) / 100);
    let failed = false;
    next.on("tileerror", ({ error }) => {
      if (version === generation) {
        failed = true;
        status(
          error?.kind === "network"
            ? error.message
            : "Some tiles could not load. Try this view again.",
          { error: true },
        );
      }
    });
    next.on("load", () => {
      if (version !== generation) return;
      if (tiles && tiles !== next) map.removeLayer(tiles);
      tiles = next;
      pendingTiles = undefined;
      $("workspace").setAttribute("aria-busy", "false");
      if (!failed) {
        $("status").textContent = selected.title;
        $("notice").hidden = true;
      }
    });
    pendingTiles = next;
    if (fitView) fit();
    next.addTo(map);
  } catch (error) {
    fail(error);
  }
}
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => apply(), 220);
}
async function updateRgb({ reset = false, fitView = false } = {}) {
  const bands = channelKeys.map((channel) => {
    const keys = [...selected.keys.slice(0, -1), $(channel).value];
    return { channel, keys, id: JSON.stringify(keys) };
  });
  const changed = bands.filter(
    ({ channel, id }) => reset || channelMetadata.get(channel) !== id,
  );
  loadingChannels = true;
  $("render-form").inert = true;
  $("auto-contrast").disabled = true;
  clearTimeout(timer);
  try {
    const results = await channelRequest.run((signal) =>
      Promise.all(changed.map(({ keys }) => client.metadata(keys, { signal }))),
    );
    changed.forEach(({ channel, id }, i) => {
      setRange(channel, autoRange(results[i]), results[i].range);
      channelMetadata.set(channel, id);
    });
  } catch (error) {
    if (error.name === "AbortError") return;
    loadingChannels = false;
    $("render-form").inert = false;
    $("auto-contrast").disabled = false;
    fail(error);
    return;
  }
  loadingChannels = false;
  $("render-form").inert = false;
  $("auto-contrast").disabled = false;
  await apply({ fitView });
}
async function choose(
  layer,
  { fitView = true, preserveStyle = false, mode = "singleband" } = {},
) {
  clearTimeout(timer);
  generation++;
  rendering.cancel();
  channelRequest.cancel();
  loadingChannels = false;
  $("auto-contrast").disabled = false;
  clearInspection();
  selected = layer;
  if (!preserveStyle) {
    $("mode").value = mode;
    $("expression").value = "";
    appliedExpression = undefined;
  }
  $("header-layer").textContent = "";
  listLayers();
  if (nativeIds) return applyNative();
  loadingSelection = true;
  $("render-form").inert = true;
  $("layer-controls").inert = true;
  status("Loading layer…", { loading: true });
  try {
    const meta = await rendering.run((signal) =>
      client.metadata(layer.keys, { signal }),
    );
    if (selected !== layer) return;
    layerMetadata = meta;
    summary(meta);
    bands();
    if (!preserveStyle) {
      setRange("single", autoRange(meta), meta.range);
    }
    loadingSelection = false;
    $("render-form").inert = false;
    $("layer-controls").inert = false;
    if ($("mode").value === "rgb") await updateRgb({ fitView });
    else await apply({ fitView });
  } catch (error) {
    if (selected === layer) {
      loadingSelection = false;
      $("render-form").inert = false;
      $("layer-controls").inert = false;
    }
    fail(error);
  }
}
async function applyNative() {
  try {
    const result = await rendering.run(async (signal) => ({
      definition: await client.nativeLayer(selected.id, { signal }),
      blob: await client.renderLayer(selected.id, {}, { signal }),
    }));
    if (nativeUrl) URL.revokeObjectURL(nativeUrl);
    nativeBlob = result.blob;
    nativeUrl = URL.createObjectURL(nativeBlob);
    $("native-image").src = nativeUrl;
    summary(result.definition);
    $("status").textContent = selected.title;
    $("notice").hidden = true;
  } catch (error) {
    fail(error);
  }
}

for (const key of ["single", "r", "g", "b"]) {
  const ids = rangeIds(key);
  for (let i = 0; i < 2; i++) {
    const slider = $(key + (i ? "-high" : "-low"));
    slider.oninput = () => {
      const other = Number($(ids[1 - i]).value),
        value = Number(slider.value);
      $(ids[i]).value = i ? Math.max(other, value) : Math.min(other, value);
      slider.value = $(ids[i]).value;
      schedule();
    };
    slider.onchange = () => apply();
    $(ids[i]).onchange = () => {
      setRange(key, rangeValue(key), [
        Number($(key + "-low").min),
        Number($(key + "-high").max),
      ]);
      apply();
    };
  }
}
$("mode").onchange = async () => {
  clearTimeout(timer);
  channelRequest.cancel();
  loadingChannels = false;
  $("render-form").inert = false;
  $("auto-contrast").disabled = false;
  showMode();
  if ($("mode").value === "rgb") return updateRgb();
  if ($("mode").value === "compute") {
    setRange("single", [-1, 1]);
  } else {
    setRange("single", autoRange(layerMetadata), layerMetadata?.range);
  }
  await apply();
};
for (const key of channelKeys) $(key).onchange = () => updateRgb();
$("colormap").onchange = () => apply();
$("color-correction").onchange = () => apply();
$("render-form").onsubmit = (event) => event.preventDefault();
$("expression-controls").onsubmit = (event) => {
  event.preventDefault();
  const expression = $("expression").value.trim();
  if (!expression) return;
  appliedExpression = { expression, v1: $("v1").value, v2: $("v2").value };
  showMode();
  apply();
};
$("auto-contrast").onclick = () => {
  if ($("mode").value === "rgb") return updateRgb({ reset: true });
  setRange("single", autoRange(layerMetadata), layerMetadata?.range);
  apply();
};
$("reset-style").onclick = () => {
  $("colormap").value = "";
  $("color-correction").value = "";
  $("opacity").value = 100;
  opacity();
  if ($("mode").value === "rgb") return updateRgb({ reset: true });
  if ($("mode").value === "compute") setRange("single", [-1, 1]);
  else setRange("single", autoRange(layerMetadata), layerMetadata?.range);
  apply();
};
$("ndvi").onclick = () => {
  $("v1").value = "nir";
  $("v2").value = "red";
  $("expression").value = "(1.0*v1-1.0*v2)/(1.0*v1+1.0*v2)";
  $("expression").focus();
};
$("time-key").onchange = timeOptions;
$("time").onchange = () =>
  choose(
    selectTime(
      discovery.layers,
      selected,
      $("time-key").value,
      $("time").value,
    ),
    { fitView: false, preserveStyle: true },
  );
for (const [id, direction] of [
  ["previous-time", -1],
  ["next-time", 1],
])
  $(id).onclick = () => {
    $("time").selectedIndex += direction;
    $("time").dispatchEvent(new Event("change"));
  };
function opacity() {
  const value = Number($("opacity").value);
  $("opacity-value").value = value + "%";
  tiles?.setOpacity(value / 100);
  pendingTiles?.setOpacity(value / 100);
}
$("opacity").oninput = opacity;
$("fit").onclick = fit;
$("retry").onclick = () =>
  !selected
    ? discover()
    : nativeIds
      ? applyNative()
      : $("mode").value === "rgb" && layerMetadata
        ? updateRgb()
        : choose(selected, { fitView: false, preserveStyle: true });

map.createPane("land").style.zIndex = 180;
map.createPane("streets").style.zIndex = 190;
const land = L.geoJSON(null, {
  pane: "land",
  interactive: false,
  attribution: '<a href="https://www.naturalearthdata.com/">Natural Earth</a>',
  style: { color: "#a0afa0", weight: 1, fillColor: "#d6ded0", fillOpacity: 1 },
});
const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  pane: "streets",
  maxNativeZoom: 19,
  maxZoom: 22,
  attribution:
    '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
});
$("basemap").onchange = () => {
  const name = $("basemap").value;
  if (name === "none") land.remove();
  else land.addTo(map);
  if (name === "streets") streets.addTo(map);
  else streets.remove();
};
$("basemap").onchange();
fetch(new URL("./natural-earth-land.geojson", import.meta.url))
  .then((response) => {
    if (!response.ok) throw new Error("Basemap unavailable");
    return response.json();
  })
  .then((data) => land.addData(data))
  .catch(() => {
    const option = $("basemap").querySelector('[value="land"]');
    option.disabled = true;
    if (option.selected) {
      $("basemap").value = "none";
      $("basemap").onchange();
    }
  });
map.on("mousemove", ({ latlng }) => {
  const p = coordinate(latlng);
  $("coordinates").hidden = false;
  $("coordinates").textContent =
    `${p.latitude.toFixed(5)}°, ${p.longitude.toFixed(5)}°`;
});
function clearInspection() {
  inspection.cancel();
  map.closePopup();
  marker?.remove();
  marker = undefined;
}
async function inspect(point) {
  if (!inspecting || !active) return;
  const p = coordinate(point);
  try {
    const result = await inspection.run(async (signal) => {
      const display = await client.inspect(
        active,
        { ...p, zoom: Math.round(map.getZoom()) },
        { signal },
      );
      let source;
      if (client.profile === "native" && active.mode === "singleband") {
        try {
          source = await client.point(active.keys, p, { signal });
        } catch (error) {
          if (error.name === "AbortError") throw error;
        }
      }
      return { display, source };
    });
    const content = document.createElement("div");
    content.id = "inspection";
    const heading = document.createElement("strong");
    heading.textContent = "Pixel";
    content.append(heading);
    const value = document.createElement("span");
    value.className = "pixel-value";
    value.textContent = result.source
      ? result.source.valid
        ? `${number(result.source.value)}${result.source.units ? " " + result.source.units : ""}`
        : "No data"
      : result.display.nodata
        ? "No data"
        : "Display color";
    content.append(value);
    const display = document.createElement("div"),
      color = document.createElement("span");
    color.className = "pixel-color";
    color.style.background = `rgba(${result.display.rgba.slice(0, 3).join(",")},${result.display.rgba[3] / 255})`;
    display.append(
      color,
      document.createTextNode(
        `Display RGBA: ${result.display.rgba.join(", ")}`,
      ),
    );
    content.append(display);
    const coords = document.createElement("div");
    coords.className = "pixel-coordinates";
    coords.textContent = `${p.latitude.toFixed(5)}°, ${p.longitude.toFixed(5)}°`;
    content.append(coords);
    if (marker) map.removeLayer(marker);
    marker = L.circleMarker(point, {
      radius: 4,
      color: "#a1492f",
      weight: 2,
      fillColor: "#fff",
      fillOpacity: 1,
    }).addTo(map);
    L.popup({
      autoPanPaddingTopLeft: mobile.matches ? [20, 20] : [380, 20],
      autoPanPaddingBottomRight: [50, mobile.matches ? 130 : 60],
    })
      .setLatLng(point)
      .setContent(content)
      .openOn(map);
  } catch (error) {
    fail(error);
  }
}
map.on("click", ({ latlng }) => inspect(latlng));
$("map").addEventListener("keydown", (event) => {
  if (event.target === $("map") && event.key === "Enter" && inspecting) {
    event.preventDefault();
    inspect(map.getCenter());
  }
});
map.on("movestart", () => inspection.cancel());
$("inspect").onclick = () => {
  inspecting = !inspecting;
  $("inspect").setAttribute("aria-pressed", String(inspecting));
  $("workspace").classList.toggle("inspecting", inspecting);
  if (!inspecting) clearInspection();
};
async function inspectNative(position) {
  if (!inspecting || !nativeBlob) return;
  try {
    const sample = await inspection.run(() =>
      inspectImage(nativeBlob, position),
    );
    status(`Display RGBA: ${sample.rgba.join(", ")}`, { transient: true });
  } catch (error) {
    fail(error);
  }
}
$("native-image").onclick = (event) => {
  const r = event.target.getBoundingClientRect();
  inspectNative([
    (event.clientX - r.left) / r.width,
    (event.clientY - r.top) / r.height,
  ]);
};
$("native-image").onkeydown = (event) => {
  if (event.key === "Enter" && inspecting) inspectNative([0.5, 0.5]);
};

async function copy(value, message, target) {
  try {
    await navigator.clipboard.writeText(value);
    target.textContent = message;
  } catch {
    target.textContent = "Select the text and copy it with your keyboard.";
  }
}
$("share").onclick = async () => {
  if (!canShare || !active) return;
  $("share-dialog").showModal();
  $("share-note").textContent = "Creating link…";
  $("share-link").value = "";
  const version = generation;
  try {
    const point = coordinate(map.getCenter());
    const state = await createState({
      baseUrl: client.baseUrl,
      profile: client.profile,
      render: active,
      sourceRevision,
      view: { center: [point.longitude, point.latitude], zoom: map.getZoom() },
      timeKey: $("time-key").value || null,
    });
    if (version !== generation)
      throw new DOMException("View was replaced", "AbortError");
    history.replaceState(null, "", encodeState(state));
    $("share-link").value = location.href;
    $("share-note").textContent =
      sourceRevision === "unversioned"
        ? "This link uses the current catalog. Its imagery may change."
        : "View link ready.";
  } catch (error) {
    if (error.name !== "AbortError")
      $("share-note").textContent =
        "Could not create a view link. " + error.message;
  }
};
$("close-share").onclick = () => $("share-dialog").close();
$("copy-share").onclick = () => {
  if ($("share-link").value)
    copy($("share-link").value, "Link copied.", $("share-note"));
};
$("share-link").onclick = () => $("share-link").select();
$("copy-metadata").onclick = () =>
  copy($("metadata").textContent, "Metadata copied.", $("copy-metadata"));
async function restore(fragment) {
  try {
    const state = await restoring.run(() =>
      restoreState(fragment, {
        baseUrl: client.baseUrl,
        profile: client.profile,
        sourceRevision,
      }),
    );
    const layer = discovery.layers.find(
      (item) =>
        JSON.stringify(item.keys) ===
        JSON.stringify(metadataKeys(state.render)),
    );
    if (!layer)
      throw new Error("The saved layer is no longer in this catalog.");
    selected = layer;
    layerMetadata = undefined;
    if (demo) $("demo-scene").value = layer.keys[0];
    bands();
    $("time-key").value = state.timeKey || "";
    timeOptions();
    $("mode").value = state.render.mode;
    const params = state.render.params;
    appliedExpression =
      state.render.mode === "compute"
        ? Object.fromEntries(
            Object.entries(params).filter(([key]) =>
              /^(expression|v[1-5])$/.test(key),
            ),
          )
        : undefined;
    showMode();
    setRange("single", params.stretch_range || params.r_range || [0, 1]);
    for (const key of ["r", "g", "b"])
      setRange(key, params[key + "_range"] || [0, 1]);
    $("colormap").value = params.colormap || "";
    for (const key of ["r", "g", "b", "v1", "v2", "expression"])
      if (params[key] !== undefined) $(key).value = params[key];
    if (state.render.mode === "rgb") {
      for (const channel of channelKeys)
        channelMetadata.set(
          channel,
          JSON.stringify([...state.render.keys, params[channel]]),
        );
    }
    const correction =
      state.render.mode === "rgb" ? params.color_transform || "" : "";
    const selector = $("color-correction");
    if (![...selector.options].some(({ value }) => value === correction))
      selector.add(new Option("Custom correction", correction));
    selector.value = correction;
    map.setView([state.view.center[1], state.view.center[0]], state.view.zoom, {
      animate: false,
    });
    await apply({ restored: state.render });
  } catch (error) {
    fail(error);
  }
}
window.addEventListener("hashchange", () => {
  if (discovery && location.hash) restore(location.hash);
});
window.addEventListener("pagehide", () => {
  clearTimeout(timer);
  rendering.cancel();
  inspection.cancel();
  restoring.cancel();
  channelRequest.cancel();
  if (nativeUrl) URL.revokeObjectURL(nativeUrl);
});
async function discover() {
  status("Reading catalog…", { loading: true });
  try {
    const connection = await connectService(serverUrl);
    client = connection.client;
    const { identity } = connection;
    sourceRevision = identity.source_revision || sourceRevision;
    canShare =
      isSecureContext &&
      !loopback(location.hostname) &&
      !loopback(serverUrl.hostname);
    try {
      discovery = await client.discover();
    } catch (error) {
      if (
        error.kind !== "http" ||
        error.status !== 400 ||
        client.profile !== "native"
      )
        throw error;
      nativeIds = (await client.nativeLayers()).layers;
    }
    $("layer-count").textContent = layers().length;
    $("share").hidden = !canShare || !!nativeIds || !layers().length;
    $("share").disabled = !layers().length || !!nativeIds;
    $("layer-controls").hidden = !layers().length || !!nativeIds;
    $("tab-style").disabled = !layers().length || !!nativeIds;
    $("tab-details").disabled = !layers().length;
    $("fit").disabled = !layers().length || !!nativeIds;
    $("inspect").disabled = !layers().length;
    $("empty").hidden = layers().length > 0;
    $("map").hidden = !!nativeIds;
    $("native-stage").hidden = !nativeIds;
    $("basemap").disabled = !!nativeIds;
    $("api-docs").href = client.baseUrl + "/apidoc";
    $("service-address").textContent = client.baseUrl;
    if (demo && !nativeIds && layers().length) {
      const scene = $("demo-scene");
      options(
        scene,
        [...new Set(layers().map((layer) => layer.keys[0]))],
        selected?.keys[0] || "lagoon",
      );
      const names = {
        lagoon: "Venice lagoon",
        dolomites: "Dolomites",
        "cape-town": "Cape Town",
      };
      for (const option of scene.options)
        option.textContent = names[option.value] || option.value;
      $("demo-scene-control").hidden = scene.options.length < 2;
    }
    if (hosted) {
      for (const link of $("pane-details").querySelectorAll("nav a")) {
        const url = new URL(link.href);
        url.searchParams.set("service", client.baseUrl);
        link.href = url;
      }
    }
    listLayers();
    if (!layers().length) {
      $("header-layer").textContent = "Empty catalog";
      $("notice").hidden = true;
      return;
    }
    if (location.hash && !nativeIds) await restore(location.hash);
    else if (demo && !nativeIds) await selectDemoScene($("demo-scene").value);
    else
      await choose(
        layers().find((layer) => layer.id === selected?.id) || layers()[0],
      );
  } catch (error) {
    fail(error);
  }
}
$("refresh").onclick = () => {
  nativeIds = undefined;
  discover();
};
document.querySelector(".brand").onclick = (event) => {
  event.preventDefault();
  tab("layers");
  fit();
};
function validateServer(url) {
  new Client(url);
  if (
    location.protocol === "https:" &&
    url.protocol === "http:" &&
    !loopback(url.hostname)
  )
    throw new Error("Enter an HTTPS tile server URL.");
}
$("change-server").hidden = !hosted;
$("change-server").onclick = () => $("server-dialog").showModal();
$("close-server").onclick = () => $("server-dialog").close();
$("server-form").onsubmit = (event) => {
  event.preventDefault();
  try {
    const url = new URL($("server-url").value);
    validateServer(url);
    pageUrl.search = "";
    pageUrl.searchParams.set("service", url.href);
    pageUrl.hash = "";
    location.assign(pageUrl);
  } catch (error) {
    $("server-error").textContent = error.message;
  }
};
panel(false);
if (hosted && !pageUrl.searchParams.has("service")) {
  $("header-layer").textContent = "";
  $("server-dialog").showModal();
} else {
  try {
    serverUrl = serviceUrl();
    validateServer(serverUrl);
    $("server-url").value = serverUrl.href;
    $("refresh").disabled = false;
    await discover();
  } catch (error) {
    fail(error);
    $("retry").hidden = true;
  }
}
