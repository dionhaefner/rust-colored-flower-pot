/** Adapters use caller-supplied map libraries. */
function size(render) {
  const [height, width] = render.params.tile_size ?? [256, 256];
  if (height !== width)
    throw new TypeError("Map integrations require square tiles");
  return width;
}
export function leafletLayer(library, client, render) {
  return library.tileLayer(client.tileTemplate(render), {
    tileSize: size(render),
    crossOrigin: "anonymous",
    noWrap: true,
    maxZoom: 22,
  });
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
