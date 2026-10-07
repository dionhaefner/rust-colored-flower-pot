/** Adapters use caller-supplied map libraries. */
function size(render) {
  const [height, width] = render.params.tile_size ?? [256, 256];
  if (height !== width)
    throw new TypeError("Map integrations require square tiles");
  return width;
}

// Share admission across replaced layers so old and new views cannot each burst.
let activeTiles = 0;
const pendingTiles = [];
function drainTiles() {
  while (activeTiles < 3 && pendingTiles.length) {
    const job = pendingTiles.shift();
    if (job.signal.aborted) continue;
    activeTiles++;
    job.run().finally(() => {
      activeTiles--;
      drainTiles();
    });
  }
}
function queuedTile(signal, operation) {
  return new Promise((resolve, reject) => {
    const job = {
      signal,
      run: async () => {
        try {
          resolve(await operation());
        } catch (error) {
          reject(error);
        }
      },
    };
    signal.addEventListener(
      "abort",
      () => {
        const index = pendingTiles.indexOf(job);
        if (index !== -1) pendingTiles.splice(index, 1);
        reject(new DOMException("Tile was removed", "AbortError"));
      },
      { once: true },
    );
    pendingTiles.push(job);
    drainTiles();
  });
}
function pacedLayer(layer, client) {
  const requests = new WeakMap();
  layer.createTile = function (coords, done) {
    const tile = document.createElement("img");
    tile.alt = "";
    const controller = new AbortController();
    const request = { controller, objectUrl: null };
    requests.set(tile, request);
    const url = this.getTileUrl(coords);
    queuedTile(controller.signal, async () => {
      for (let attempt = 0; ; attempt++) {
        try {
          return await client.request(url, {
            signal: controller.signal,
            response: "blob",
          });
        } catch (error) {
          if (
            error.status !== 503 ||
            attempt === 2 ||
            controller.signal.aborted
          )
            throw error;
          await new Promise((resolve) =>
            setTimeout(resolve, 150 * (attempt + 1)),
          );
        }
      }
    })
      .then((blob) => {
        if (controller.signal.aborted) return;
        request.objectUrl = URL.createObjectURL(blob);
        tile.onload = () => done(null, tile);
        tile.onerror = () =>
          done(new Error("Tile image could not be decoded"), tile);
        tile.src = request.objectUrl;
      })
      .catch((error) => {
        if (!controller.signal.aborted) done(error, tile);
      });
    return tile;
  };
  layer.on("tileunload", ({ tile }) => {
    const request = requests.get(tile);
    if (!request) return;
    request.controller.abort();
    tile.onload = tile.onerror = null;
    if (request.objectUrl) URL.revokeObjectURL(request.objectUrl);
  });
  return layer;
}
export function leafletLayer(library, client, render) {
  return pacedLayer(
    library.tileLayer(client.tileTemplate(render), {
      tileSize: size(render),
      crossOrigin: "anonymous",
      noWrap: true,
      maxZoom: 22,
    }),
    client,
  );
}
export function mapLibreSource(client, render) {
  return {
    type: "raster",
    tiles: [client.tileTemplate(render)],
    tileSize: size(render),
    minzoom: 0,
    maxzoom: 22,
  };
}
export function openLayersSource(library, client, render) {
  return new library.source.XYZ({
    url: client.tileTemplate(render),
    tileSize: size(render),
    crossOrigin: "anonymous",
    wrapX: false,
    maxZoom: 22,
  });
}

/** TileJSON 3.0 adapters for server-discovered Web Mercator tilesets. */
export function leafletTileJSON(library, tilejson) {
  return library.tileLayer(tilejson.tiles[0], {
    tileSize: tilejson.tileSize,
    minZoom: tilejson.minzoom,
    maxZoom: tilejson.maxzoom,
    attribution: tilejson.attribution,
    crossOrigin: "anonymous",
    noWrap: true,
    bounds: [
      [tilejson.bounds[1], tilejson.bounds[0]],
      [tilejson.bounds[3], tilejson.bounds[2]],
    ],
  });
}
export function mapLibreTileJSONSource(tilejson) {
  return {
    type: "raster",
    tiles: tilejson.tiles,
    tileSize: tilejson.tileSize,
    minzoom: tilejson.minzoom,
    maxzoom: tilejson.maxzoom,
    bounds: tilejson.bounds,
    attribution: tilejson.attribution,
  };
}
export function openLayersTileJSONSource(library, tilejson) {
  return new library.source.TileJSON({
    tileJSON: tilejson,
    crossOrigin: "anonymous",
    wrapX: false,
  });
}
