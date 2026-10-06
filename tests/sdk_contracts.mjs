import test from "node:test";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import {
  Client,
  VERSION,
  RcfpError,
  LatestRequest,
  createState,
  encodeState,
  restoreState,
  temporalValues,
  selectTime,
} from "../js/sdk/index.js";
import {
  mapLibreSource,
  leafletLayer,
  openLayersSource,
} from "../js/sdk/integrations.js";
const render = {
  mode: "singleband",
  keys: ["scene / +%?#ü", "red"],
  params: { stretch_range: [0, 3000], colormap: "viridis" },
};
const client = new Client("https://example.test/revision/");
const input = {
  baseUrl: client.baseUrl,
  profile: "native",
  sourceRevision: "public-r1",
  render,
  view: { center: [12.36, 45.46], zoom: 12 },
  timeKey: null,
};
test("URLs preserve base paths, components, query JSON, and map placeholders", () => {
  const url = new URL(client.tileUrl(render, { z: 12, x: 2188, y: 1466 }));
  assert.equal(
    url.pathname,
    "/revision/singleband/scene%20%2F%20%2B%25%3F%23%C3%BC/red/12/2188/1466.png",
  );
  assert.equal(url.searchParams.get("stretch_range"), "[0,3000]");
  assert.equal(
    client.tileTemplate(render).split("?")[0],
    "https://example.test/revision/singleband/scene%20%2F%20%2B%25%3F%23%C3%BC/red/{z}/{x}/{y}.png",
  );
  assert.match(client.previewUrl(render), /preview.png/);
  assert.throws(() => client.tileUrl(render, { z: 2, x: -1, y: 0 }));
  assert.throws(() => client.tileTemplate({ ...render, keys: [".."] }));
});
test("all map adapters consume the same SDK template", () => {
  const expected = client.tileTemplate(render);
  assert.equal(mapLibreSource(client, render).tiles[0], expected);
  assert.equal(
    leafletLayer(
      { tileLayer: (url, options) => ({ url, options }) },
      client,
      render,
    ).url,
    expected,
  );
  class XYZ {
    constructor(options) {
      this.options = options;
    }
  }
  assert.equal(
    openLayersSource({ source: { XYZ } }, client, render).options.url,
    expected,
  );
  assert.throws(() =>
    mapLibreSource(client, { ...render, params: { tile_size: [128, 256] } }),
  );
});
test("request transport keeps authentication out of URLs/state and distinguishes errors", async () => {
  let captured;
  const api = new Client(client.baseUrl, {
    fetch: async (url, options) => {
      captured = { url, options };
      return new Response(JSON.stringify({ keys: [] }));
    },
  });
  await api.batchMetadata([render.keys]);
  assert.equal(captured.options.method, "POST");
  assert.equal(captured.options.credentials, "omit");
  assert.equal(captured.options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(captured.options.body), { keys: [render.keys] });
  const missing = new Client(client.baseUrl, {
    fetch: async () => new Response('{"error":"missing"}', { status: 404 }),
  });
  await assert.rejects(
    missing.metadata(["missing"]),
    (error) =>
      error instanceof RcfpError &&
      error.kind === "http" &&
      error.status === 404 &&
      error.detail.error === "missing",
  );
  const offline = new Client(client.baseUrl, {
    fetch: async () => {
      throw new TypeError("fetch failed");
    },
  });
  await assert.rejects(offline.keys(), (error) => error.kind === "network");
  const bad = new Client(client.baseUrl, {
    fetch: async () => new Response("not JSON"),
  });
  await assert.rejects(bad.keys(), (error) => error.kind === "contract");
});
test("discovery paginates and refuses truncated catalogs", async () => {
  const calls = [];
  const api = new Client(client.baseUrl, {
    fetch: async (url) => {
      const parsed = new URL(url);
      calls.push(parsed);
      if (parsed.pathname.endsWith("/keys"))
        return Response.json({ keys: [{ key: "scene" }, { key: "band" }] });
      return Response.json({
        datasets:
          parsed.searchParams.get("page") === "0"
            ? Array.from({ length: 1000 }, (_, i) => ({
                scene: String(i),
                band: "red",
              }))
            : [{ scene: "last", band: "red" }],
      });
    },
  });
  assert.equal((await api.discover()).layers.length, 1001);
  assert.equal(calls.length, 3);
  await assert.rejects(api.discover({ maxPages: 1 }), /page bound/);
});
test("profiles are immutable server policy and gate native additions", async () => {
  const api = new Client(client.baseUrl, {
    profile: "terracotta-0.10.1",
    fetch: async () =>
      Response.json({
        protocol_compatibility: "0.10.1",
        compatibility_profile: "native",
      }),
  });
  await assert.rejects(api.connect(), (error) => error.kind === "profile");
  assert.throws(
    () => api.tileTemplate({ ...render, params: { band: 2 } }),
    (error) => error.kind === "profile",
  );
  assert.throws(
    () => api.nativeLayers(),
    (error) => error.kind === "profile",
  );
  assert.throws(() => new Client(client.baseUrl, { profile: "unknown" }));
});
test("LatestRequest rejects stale success and stale failure, and aborts the old signal", async () => {
  const latest = new LatestRequest();
  let resolve, oldSignal;
  const old = latest.run((signal) => {
    oldSignal = signal;
    return new Promise((done) => {
      resolve = done;
    });
  });
  const rejected = assert.rejects(old, (error) => error.name === "AbortError");
  assert.equal(await latest.run(async () => "new"), "new");
  assert.ok(oldSignal.aborted);
  resolve("old");
  await rejected;
  let reject;
  const staleError = latest.run(
    () =>
      new Promise((done, failed) => {
        reject = failed;
      }),
  );
  const rejection = assert.rejects(
    staleError,
    (error) => error.name === "AbortError",
  );
  latest.cancel();
  reject(new Error("old failure"));
  await rejection;
});
test("time browsing keeps all non-time keys fixed and does not infer temporal semantics", () => {
  const rows = ["2024-01-01", "2024-01-02"].map((time) => ({
    values: { scene: "A", time, band: "red" },
    keys: ["A", time, "red"],
  }));
  rows.push({ values: { scene: "B", time: "2024-01-03", band: "red" } });
  assert.deepEqual(temporalValues(rows, rows[0], "time"), [
    "2024-01-01",
    "2024-01-02",
  ]);
  assert.equal(selectTime(rows, rows[0], "time", "2024-01-02"), rows[1]);
  assert.throws(() => selectTime(rows, rows[0], "time", "2024-01-03"));
  assert.throws(() => temporalValues(rows, rows[0], "unknown"));
});
test("shared state verifies source/profile/render revisions, roundtrips Unicode, and rejects secrets", async () => {
  const state = await createState(input),
    encoded = encodeState(state);
  assert.deepEqual(await restoreState(encoded, input), state);
  assert.equal(state.renderRevision.length, 64);
  await assert.rejects(
    restoreState(encoded, { ...input, sourceRevision: "public-r2" }),
  );
  await assert.rejects(
    restoreState(encoded, { ...input, profile: "terracotta-0.10.1" }),
  );
  await assert.rejects(
    restoreState(encoded, { ...input, baseUrl: "https://other.test" }),
  );
  await assert.rejects(
    restoreState(
      encodeState({
        ...state,
        render: { ...render, params: { stretch_range: [0, 100] } },
      }),
      input,
    ),
    /Render revision/,
  );
  await assert.rejects(
    restoreState(encodeState({ ...state, token: "secret" }), input),
  );
  await assert.rejects(
    createState({
      ...input,
      render: { ...render, params: { token: "secret" } },
    }),
  );
  await assert.rejects(
    createState({ ...input, baseUrl: "https://user:secret@example.test" }),
  );
  await assert.rejects(
    createState({ ...input, baseUrl: "https://example.test?token=secret" }),
  );
  await assert.rejects(createState({ ...input, sourceRevision: "" }));
  await assert.rejects(restoreState("#rcfp=garbage", input));
});

test("package, exported version and declaration agree", () => {
  const metadata = JSON.parse(
    readFileSync(new URL("../js/sdk/package.json", import.meta.url), "utf8"),
  );
  const declarations = readFileSync(
    new URL("../js/sdk/index.d.ts", import.meta.url),
    "utf8",
  );
  assert.equal(metadata.version, VERSION);
  assert.ok(declarations.includes(`export const VERSION: "${VERSION}"`));
  assert.deepEqual(Object.keys(metadata.exports), [".", "./integrations"]);
});
test("fetch is called independently of the Client receiver", async () => {
  const api = new Client(client.baseUrl, {
    fetch: function () {
      assert.equal(this, undefined);
      return Promise.resolve(Response.json({ keys: [] }));
    },
  });
  await api.keys();
  assert.throws(() => {
    api.profile = "terracotta-0.10.1";
  }, TypeError);
  assert.throws(() => {
    api.baseUrl = "https://other.test";
  }, TypeError);
});
test("literal braces in keys and selectors remain encoded in map templates", () => {
  const rgb = {
    mode: "rgb",
    keys: ["{z}", "{x}"],
    params: { r: "{y}", g: "green", b: "blue" },
  };
  const url = client.tileTemplate(rgb);
  assert.ok(url.includes("/%7Bz%7D/%7Bx%7D/{z}/{x}/{y}.png"));
  assert.ok(url.includes("r=%7By%7D"));
});
