import type { Client, Render } from "./index.js";
export function leafletLayer<T>(
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
