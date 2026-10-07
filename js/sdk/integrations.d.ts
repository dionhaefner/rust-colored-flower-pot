import type { Client, Render } from "./index.js";
export interface LeafletTileLayer {
  getTileUrl(coords: { x: number; y: number; z: number }): string;
  on(
    event: "tileunload",
    callback: (event: { tile: HTMLImageElement }) => void,
  ): unknown;
}
export function leafletLayer<T extends LeafletTileLayer>(
  library: {
    tileLayer(
      url: string,
      options: {
        tileSize: number;
        crossOrigin: string;
        noWrap: boolean;
        maxZoom: number;
      },
    ): T;
  },
  client: Client,
  render: Render,
): T;
export function mapLibreSource(
  client: Client,
  render: Render,
): {
  type: "raster";
  tiles: string[];
  tileSize: number;
  minzoom: number;
  maxzoom: number;
};
export function openLayersSource<T>(
  library: {
    source: {
      XYZ: new (options: {
        url: string;
        tileSize: number;
        crossOrigin: string;
        wrapX: boolean;
        maxZoom: number;
      }) => T;
    };
  },
  client: Client,
  render: Render,
): T;

export function leafletTileJSON<T>(
  library: {
    tileLayer(
      url: string,
      options: {
        tileSize: number;
        minZoom: number;
        maxZoom: number;
        attribution: string;
        crossOrigin: string;
        noWrap: boolean;
        bounds: number[][];
      },
    ): T;
  },
  tilejson: import("./index.js").TileJSON,
): T;
export function mapLibreTileJSONSource(
  tilejson: import("./index.js").TileJSON,
): {
  type: "raster";
  tiles: string[];
  tileSize: number;
  minzoom: number;
  maxzoom: number;
  bounds: [number, number, number, number];
  attribution: string;
};
export function openLayersTileJSONSource<T>(
  library: {
    source: {
      TileJSON: new (options: {
        tileJSON: import("./index.js").TileJSON;
        crossOrigin: string;
        wrapX: boolean;
      }) => T;
    };
  },
  tilejson: import("./index.js").TileJSON,
): T;
