/** Public, dependency-free RCFP browser SDK. */
export const VERSION = "0.1.0";
const profiles = ["native", "terracotta"];
const parameters = {
  singleband: [
    "stretch_range",
    "colormap",
    "explicit_color_map",
    "color_transform",
    "tile_size",
    "band",
  ],
  rgb: [
    "r",
    "g",
    "b",
    "r_range",
    "g_range",
    "b_range",
    "color_transform",
    "tile_size",
    "r_band",
    "g_band",
    "b_band",
    "format",
    "quality",
  ],
  compute: [
    "expression",
    "v1",
    "v2",
    "v3",
    "v4",
    "v5",
    "stretch_range",
    "colormap",
    "explicit_color_map",
    "tile_size",
  ],
};
export class RcfpError extends Error {
  constructor(message, { kind = "contract", status = 0, detail = null } = {}) {
    super(message);
    this.name = "RcfpError";
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}
function fail(message, kind = "contract") {
  throw new RcfpError(message, { kind });
}
function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function exact(value, allowed) {
  if (!object(value) || Object.keys(value).some((k) => !allowed.includes(k)))
    fail("Unknown or invalid fields");
}
function profile(value) {
  if (!profiles.includes(value))
    fail("Unknown compatibility profile", "profile");
  return value;
}
function base(value) {
  const url = new URL(value);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    fail(
      "Service URL must be HTTP(S) without credentials, query or fragment",
      "state",
    );
  return url.href.replace(/\/$/, "");
}
function tilesetId(keys) {
  if (!Array.isArray(keys) || !keys.length)
    fail("Tileset selection requires dataset keys");
  keys.forEach(encode);
  return (
    "k-" +
    Array.from(new TextEncoder().encode(JSON.stringify(keys)), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("")
  );
}
function encode(value) {
  if (typeof value !== "string" || !value || value === "." || value === "..")
    fail("Key components must be nonempty strings other than dot segments");
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase(),
  );
}
function canonical(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value))
    return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (object(value))
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map((k) => JSON.stringify(k) + ":" + canonical(value[k]))
        .join(",") +
      "}"
    );
  fail("State must contain finite JSON values", "state");
}
function validateRender(render, selectedProfile) {
  exact(render, ["mode", "keys", "params"]);
  if (
    !parameters[render.mode] ||
    !Array.isArray(render.keys) ||
    !object(render.params)
  )
    fail("Invalid rendering request");
  render.keys.forEach(encode);
  exact(render.params, parameters[render.mode]);
  canonical(render.params);
  if (
    selectedProfile === "terracotta" &&
    ["band", "r_band", "g_band", "b_band", "format", "quality"].some(
      (k) => k in render.params,
    )
  )
    fail("Rendering option requires the native profile", "profile");
  if (
    render.mode === "rgb" &&
    ["r", "g", "b"].some(
      (k) => typeof render.params[k] !== "string" || !render.params[k],
    )
  )
    fail("RGB requires r, g and b selectors");
  if (
    render.mode === "compute" &&
    (typeof render.params.expression !== "string" ||
      !render.params.expression ||
      !Array.isArray(render.params.stretch_range))
  )
    fail("Compute requires an expression and explicit stretch");
  return render;
}
async function digest(value) {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonical(value)),
  );
  return [...new Uint8Array(bytes)]
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
}
/** Abort replaced requests and reject their late results. */
export class LatestRequest {
  #generation = 0;
  #controller;
  cancel() {
    this.#generation++;
    this.#controller?.abort();
  }
  async run(operation) {
    this.cancel();
    const generation = this.#generation;
    const controller = (this.#controller = new AbortController());
    try {
      const result = await operation(controller.signal);
      if (generation !== this.#generation || controller.signal.aborted)
        throw new DOMException("View was replaced", "AbortError");
      return result;
    } catch (error) {
      if (generation !== this.#generation || controller.signal.aborted)
        throw new DOMException("View was replaced", "AbortError");
      throw error;
    }
  }
}
export class Client {
  constructor(
    serviceUrl,
    {
      profile: selectedProfile = "native",
      fetch: fetcher = globalThis.fetch,
    } = {},
  ) {
    Object.defineProperties(this, {
      baseUrl: { value: base(serviceUrl), enumerable: true },
      profile: { value: profile(selectedProfile), enumerable: true },
    });
    this.fetch = (...args) => fetcher(...args);
  }
  url(components, query = {}) {
    const url = new URL(this.baseUrl + "/" + components.map(encode).join("/"));
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined)
        url.searchParams.set(
          key,
          typeof value === "object" ? JSON.stringify(value) : String(value),
        );
    }
    return url.href;
  }
  async request(url, { signal, body, response = "json" } = {}) {
    let result;
    try {
      result = await this.fetch(url, {
        signal,
        credentials: "omit",
        ...(body === undefined
          ? {}
          : {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }),
      });
    } catch (error) {
      if (signal?.aborted || error.name === "AbortError") throw error;
      throw new RcfpError(
        "Request failed; check service availability and CORS",
        { kind: "network" },
      );
    }
    if (!result.ok) {
      let detail = null;
      try {
        detail = await result.json();
      } catch (error) {
        if (signal?.aborted || error.name === "AbortError") throw error;
        /* HTML errors do not define a JSON contract. */
      }
      throw new RcfpError(`HTTP ${result.status}`, {
        kind: "http",
        status: result.status,
        detail,
      });
    }
    if (response === "blob") return result.blob();
    try {
      return await result.json();
    } catch (error) {
      if (signal?.aborted || error.name === "AbortError") throw error;
      fail("Server returned invalid JSON");
    }
  }
  async connect(options) {
    const identity = await this.request(
      this.url(["__terracotta", "identity"]),
      options,
    );
    if (
      identity.protocol_compatibility !== "0.10.1" ||
      identity.compatibility_profile !== this.profile
    )
      fail("Service protocol or profile does not match the client", "profile");
    return identity;
  }
  keys(options) {
    return this.request(this.url(["keys"]), options);
  }
  datasets({
    filters = {},
    page = 0,
    limit = 1000,
    orderBy,
    ascending = true,
    signal,
  } = {}) {
    if (
      !Number.isInteger(page) ||
      page < 0 ||
      !Number.isInteger(limit) ||
      limit < 1 ||
      limit > 1000
    )
      fail("Invalid dataset page");
    if (
      Object.keys(filters).some((k) =>
        ["page", "limit", "order_by", "ascending"].includes(k),
      )
    )
      fail("Reserved dataset filter");
    // Both profiles accept the bracketed list syntax, including single-key ordering.
    const order =
      orderBy === undefined ? undefined : "[" + orderBy.join(",") + "]";
    const encodedFilters = Object.fromEntries(
      Object.entries(filters).map(([key, value]) => {
        if (Array.isArray(value)) {
          if (
            !value.length ||
            value.some((v) => typeof v !== "string" || /[\[\],]/.test(v))
          )
            fail(
              "List filters require nonempty strings without brackets or commas",
            );
          return [key, "[" + value.join(",") + "]"];
        }
        if (typeof value !== "string")
          fail("Filters must be strings or string arrays");
        return [key, value];
      }),
    );
    return this.request(
      this.url(["datasets"], {
        ...encodedFilters,
        page,
        limit,
        order_by: order,
        ascending,
      }),
      { signal },
    );
  }
  async discover({ signal, maxPages = 100 } = {}) {
    if (!Number.isInteger(maxPages) || maxPages < 1)
      fail("Invalid discovery page bound");
    const { keys } = await this.keys({ signal });
    if (!Array.isArray(keys) || keys.some((k) => typeof k.key !== "string"))
      fail("Invalid key discovery response");
    const layers = [];
    for (let page = 0; page < maxPages; page++) {
      const result = await this.datasets({ page, signal });
      if (!Array.isArray(result.datasets))
        fail("Invalid dataset discovery response");
      for (const row of result.datasets) {
        const values = keys.map((k) => row[k.key]);
        values.forEach(encode);
        layers.push({
          kind: "dataset",
          id: JSON.stringify(values),
          keys: values,
          values: Object.fromEntries(keys.map((k, i) => [k.key, values[i]])),
          title: values.join(" / "),
        });
      }
      if (result.datasets.length < 1000) return { keys, layers };
    }
    fail(
      "Discovery exceeded its page bound; narrow the catalog or use paginated datasets",
    );
  }
  tilesets({ signal, page = 0, limit = 1000 } = {}) {
    if (this.profile !== "native")
      fail("Tilesets require the native profile", "profile");
    if (
      !Number.isInteger(page) ||
      page < 0 ||
      !Number.isInteger(limit) ||
      limit < 1 ||
      limit > 1000
    )
      fail("Invalid tileset pagination");
    return this.request(this.url(["tilesets"], { page, limit }), { signal });
  }
  async discoverTilesets({ signal, maxPages = 100 } = {}) {
    if (!Number.isInteger(maxPages) || maxPages < 1)
      fail("Invalid discovery page bound");
    const tilesets = [];
    for (let page = 0; page < maxPages; page++) {
      const result = await this.tilesets({ signal, page });
      if (!Array.isArray(result.tilesets))
        fail("Invalid tileset discovery response");
      tilesets.push(...result.tilesets);
      if (result.tilesets.length < 1000) return { tilesets };
    }
    fail("Tileset discovery exceeded its page bound");
  }
  tileJSON(keys, params = {}, options) {
    if (this.profile !== "native")
      fail("TileJSON requires the native profile", "profile");
    if (!Array.isArray(keys) || !keys.length)
      fail("TileJSON requires dataset keys");
    return this.request(
      this.url(["tilejson", tilesetId(keys) + ".json"], params),
      options,
    );
  }
  point(keys, { longitude, latitude }, options) {
    if (this.profile !== "native")
      fail("Source inspection requires the native profile", "profile");
    if (
      !Array.isArray(keys) ||
      !keys.length ||
      !Number.isFinite(longitude) ||
      longitude < -180 ||
      longitude > 180 ||
      !Number.isFinite(latitude) ||
      latitude < -90 ||
      latitude > 90
    )
      fail("Invalid point selection");
    return this.request(
      this.url(["point", tilesetId(keys)], { lon: longitude, lat: latitude }),
      options,
    );
  }
  metadata(keys, options) {
    return this.request(this.url(["metadata", ...keys]), options);
  }
  batchMetadata(keys, options) {
    return this.request(this.url(["metadata"]), { ...options, body: { keys } });
  }
  legend({ colormap, stretch_range, num_values = 16 }, options) {
    return this.request(
      this.url(["colormap"], { colormap, stretch_range, num_values }),
      options,
    );
  }
  tileUrl(render, { z, x, y }) {
    validateRender(render, this.profile);
    if (
      !Number.isInteger(z) ||
      z < 0 ||
      z > 30 ||
      !Number.isInteger(x) ||
      !Number.isInteger(y) ||
      x < 0 ||
      y < 0 ||
      x >= 2 ** z ||
      y >= 2 ** z
    )
      fail("Invalid XYZ coordinate");
    return this.url(
      [render.mode, ...render.keys, String(z), String(x), `${y}.png`],
      render.params,
    );
  }
  tileTemplate(render) {
    const url = new URL(this.tileUrl(render, { z: 0, x: 0, y: 0 }));
    const path = url.pathname.replace(/\/0\/0\/0\.png$/, "/{z}/{x}/{y}.png");
    return url.origin + path + url.search;
  }
  previewUrl(render) {
    validateRender(render, this.profile);
    if (["band", "r_band", "g_band", "b_band"].some((k) => k in render.params))
      fail("Source-band selection is supported on tiles only");
    return this.url(
      [render.mode, ...render.keys, "preview.png"],
      render.params,
    );
  }
  preview(render, options) {
    return this.request(this.previewUrl(render), {
      ...options,
      response: "blob",
    });
  }
  tile(render, coordinate, options) {
    return this.request(this.tileUrl(render, coordinate), {
      ...options,
      response: "blob",
    });
  }
  nativeLayers(options) {
    if (this.profile !== "native")
      fail("Native layers require the native profile", "profile");
    return this.request(this.url(["layers"]), options);
  }
  nativeLayer(id, options) {
    if (this.profile !== "native")
      fail("Native layers require the native profile", "profile");
    return this.request(this.url(["layers", id]), options);
  }
  renderLayer(id, spec = {}, options) {
    if (this.profile !== "native")
      fail("Native layers require the native profile", "profile");
    return this.request(this.url(["layers", id, "render"]), {
      ...options,
      body: spec,
      response: "blob",
    });
  }
  /** Inspect the displayed tile sample, not a source/analysis value. */
  async inspect(render, { longitude, latitude, zoom }, options) {
    if (
      !Number.isFinite(longitude) ||
      longitude < -180 ||
      longitude >= 180 ||
      !Number.isFinite(latitude) ||
      Math.abs(latitude) > 85.05112878 ||
      !Number.isInteger(zoom) ||
      zoom < 0 ||
      zoom > 30
    )
      fail("Invalid inspection coordinate");
    const n = 2 ** zoom;
    const tx = ((longitude + 180) / 360) * n;
    const radians = (latitude * Math.PI) / 180;
    const ty = Math.max(
      0,
      Math.min(
        n - Number.EPSILON,
        ((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2) * n,
      ),
    );
    const x = Math.floor(tx),
      y = Math.min(n - 1, Math.floor(ty));
    const blob = await this.tile(render, { z: zoom, x, y }, options);
    return inspectImage(
      blob,
      [tx - x, Math.min(1 - Number.EPSILON, ty - y)],
      options,
    );
  }
}
export async function inspectImage(blob, [u, v], { signal } = {}) {
  if (![u, v].every((n) => Number.isFinite(n) && n >= 0 && n < 1))
    fail("Inspection must be inside the image");
  signal?.throwIfAborted();
  const bitmap = await createImageBitmap(blob);
  try {
    signal?.throwIfAborted();
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0);
    const pixel = [Math.floor(u * bitmap.width), Math.floor(v * bitmap.height)];
    const rgba = [...context.getImageData(...pixel, 1, 1).data];
    return {
      kind: "display",
      rgba,
      nodata: rgba[3] === 0,
      pixel,
      size: [bitmap.height, bitmap.width],
    };
  } finally {
    bitmap.close();
  }
}
export function temporalValues(layers, selected, timeKey) {
  if (!(timeKey in selected.values))
    fail("Time key is not part of the selected dataset");
  return [
    ...new Set(
      layers
        .filter((layer) =>
          Object.keys(selected.values).every(
            (k) => k === timeKey || layer.values[k] === selected.values[k],
          ),
        )
        .map((layer) => layer.values[timeKey]),
    ),
  ].sort();
}
export function selectTime(layers, selected, timeKey, value) {
  const result = layers.find(
    (layer) =>
      layer.values[timeKey] === value &&
      Object.keys(selected.values).every(
        (k) => k === timeKey || layer.values[k] === selected.values[k],
      ),
  );
  if (!result) fail("No dataset at the selected time");
  return result;
}
export async function createState({
  baseUrl,
  profile: selectedProfile,
  render,
  sourceRevision,
  view,
  timeKey = null,
}) {
  if (
    typeof sourceRevision !== "string" ||
    !sourceRevision.trim() ||
    sourceRevision.length > 256
  )
    fail("A public source revision is required", "state");
  profile(selectedProfile);
  validateRender(render, selectedProfile);
  exact(view, ["center", "zoom"]);
  if (
    !Array.isArray(view.center) ||
    view.center.length !== 2 ||
    !view.center.every(Number.isFinite) ||
    view.center[0] < -180 ||
    view.center[0] > 180 ||
    Math.abs(view.center[1]) > 85.05112878 ||
    !Number.isFinite(view.zoom) ||
    view.zoom < 0 ||
    view.zoom > 22
  )
    fail("Invalid map view", "state");
  if (timeKey !== null && typeof timeKey !== "string")
    fail("Invalid time key", "state");
  return JSON.parse(
    canonical({
      version: 1,
      baseUrl: base(baseUrl),
      profile: selectedProfile,
      render,
      sourceRevision,
      renderRevision: await digest(render),
      view,
      timeKey,
    }),
  );
}
export function encodeState(state) {
  const json = canonical(state);
  if (json.length > 12000)
    fail("Shared state exceeds 12000 characters", "state");
  return "#rcfp=" + encodeURIComponent(json);
}
export async function restoreState(fragment, expected) {
  if (!fragment.startsWith("#rcfp=") || fragment.length > 72006)
    fail("Invalid shared state", "state");
  let state;
  try {
    state = JSON.parse(decodeURIComponent(fragment.slice(6)));
  } catch {
    fail("Invalid shared state JSON", "state");
  }
  exact(state, [
    "version",
    "baseUrl",
    "profile",
    "render",
    "sourceRevision",
    "renderRevision",
    "view",
    "timeKey",
  ]);
  if (state.version !== 1) fail("Unsupported shared-state version", "state");
  const validated = await createState(state);
  if (validated.renderRevision !== state.renderRevision)
    fail("Render revision does not match its specification", "state");
  if (
    !expected ||
    base(expected.baseUrl) !== validated.baseUrl ||
    expected.profile !== validated.profile ||
    expected.sourceRevision !== validated.sourceRevision
  )
    fail("Source URL, revision or profile has changed", "state");
  return validated;
}
