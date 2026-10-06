export const VERSION: "0.1.0";
export type Profile = "native" | "terracotta-0.10.1";
export type Range = [
  number | `p${number}` | null,
  number | `p${number}` | null,
];
export type Size = [number, number];
export type Rgba = [number, number, number, number];
export interface RequestOptions {
  signal?: AbortSignal;
}
export interface CommonParams {
  tile_size?: Size;
}
export interface SinglebandParams extends CommonParams {
  stretch_range?: Range;
  colormap?: string;
  explicit_color_map?: [number, Rgba][];
  color_transform?: string;
  band?: number;
}
export interface RgbParams extends CommonParams {
  r: string;
  g: string;
  b: string;
  r_range?: Range;
  g_range?: Range;
  b_range?: Range;
  color_transform?: string;
  r_band?: number;
  g_band?: number;
  b_band?: number;
  format?: "png" | "webp";
  quality?: number;
}
export interface ComputeParams extends CommonParams {
  expression: string;
  stretch_range: [number, number];
  v1?: string;
  v2?: string;
  v3?: string;
  v4?: string;
  v5?: string;
  colormap?: string;
  explicit_color_map?: [number, Rgba][];
}
export type Render =
  | { mode: "singleband"; keys: string[]; params: SinglebandParams }
  | { mode: "rgb"; keys: string[]; params: RgbParams }
  | { mode: "compute"; keys: string[]; params: ComputeParams };
export interface Key {
  key: string;
  description?: string;
}
export interface DatasetLayer {
  kind: "dataset";
  id: string;
  keys: string[];
  values: Record<string, string>;
  title: string;
}
export interface Discovery {
  keys: Key[];
  layers: DatasetLayer[];
}
export interface Metadata {
  bounds: [number, number, number, number];
  range: [number, number];
  percentiles: number[];
  [field: string]: unknown;
}
export interface Identity {
  implementation: string;
  protocol_compatibility: string;
  database_compatibility: string;
  source_revision?: string;
  compatibility_profile: Profile;
}
export interface DisplaySample {
  kind: "display";
  rgba: Rgba;
  nodata: boolean;
  pixel: [number, number];
  size: Size;
}
export interface NativeRender {
  bindings: Record<string, { asset: string; band: string | number }>;
  values: "raw" | "scaled";
  channels: {
    expression: string;
    stretch: [number, number] | null;
    categories?: { value: number; color: Rgba }[];
  }[];
  coverage: "any_valid" | "all_valid";
  grid: {
    crs: string;
    bounds: [number, number, number, number];
    size: Size;
    resampling: "nearest" | "bilinear" | "cubic" | "average";
  };
  encoding: "png";
}
export interface NativeLayer {
  observations: {
    id: string;
    assets: Record<
      string,
      {
        location: string;
        revision: string;
        band_names?: Record<string, number>;
        metadata?: unknown;
      }
    >;
    attributes: Record<string, unknown>;
  }[];
  overlap: "first_valid" | "last_valid";
  selection: Record<string, unknown>;
  default_render: NativeRender | null;
}
export class RcfpError extends Error {
  constructor(
    message: string,
    options?: { kind?: string; status?: number; detail?: unknown },
  );
  kind: string;
  status: number;
  detail: unknown;
}
export class LatestRequest {
  cancel(): void;
  run<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T>;
}
export class Client {
  constructor(
    serviceUrl: string,
    options?: { profile?: Profile; fetch?: typeof fetch },
  );
  readonly baseUrl: string;
  readonly profile: Profile;
  url(components: string[], query?: Record<string, unknown>): string;
  request<T = unknown>(
    url: string,
    options?: RequestOptions & { body?: unknown; response?: "json" },
  ): Promise<T>;
  request(
    url: string,
    options: RequestOptions & { body?: unknown; response: "blob" },
  ): Promise<Blob>;
  connect(options?: RequestOptions): Promise<Identity>;
  keys(options?: RequestOptions): Promise<{ keys: Key[] }>;
  datasets(
    options?: RequestOptions & {
      filters?: Record<string, string | string[]>;
      page?: number;
      limit?: number;
      orderBy?: string[];
      ascending?: boolean;
    },
  ): Promise<{
    datasets: Record<string, string>[];
    page: number;
    limit: number;
  }>;
  discover(
    options?: RequestOptions & { maxPages?: number },
  ): Promise<Discovery>;
  metadata(keys: string[], options?: RequestOptions): Promise<Metadata>;
  batchMetadata(
    keys: string[][],
    options?: RequestOptions,
  ): Promise<Metadata[]>;
  legend(
    params: {
      colormap?: string;
      stretch_range: [number, number];
      num_values?: number;
    },
    options?: RequestOptions,
  ): Promise<{ colormap: { value: number; rgba: Rgba }[] }>;
  tileUrl(
    render: Render,
    coordinate: { z: number; x: number; y: number },
  ): string;
  tileTemplate(render: Render): string;
  previewUrl(render: Render): string;
  preview(render: Render, options?: RequestOptions): Promise<Blob>;
  tile(
    render: Render,
    coordinate: { z: number; x: number; y: number },
    options?: RequestOptions,
  ): Promise<Blob>;
  inspect(
    render: Render,
    point: { longitude: number; latitude: number; zoom: number },
    options?: RequestOptions,
  ): Promise<DisplaySample>;
  nativeLayers(
    options?: RequestOptions,
  ): Promise<{ format: "rcfp-native-1"; layers: string[] }>;
  nativeLayer(id: string, options?: RequestOptions): Promise<NativeLayer>;
  renderLayer(
    id: string,
    spec?: NativeRender | Record<string, never>,
    options?: RequestOptions,
  ): Promise<Blob>;
}
export function inspectImage(
  blob: Blob,
  uv: [number, number],
  options?: RequestOptions,
): Promise<DisplaySample>;
export function temporalValues(
  layers: DatasetLayer[],
  selected: DatasetLayer,
  timeKey: string,
): string[];
export function selectTime(
  layers: DatasetLayer[],
  selected: DatasetLayer,
  timeKey: string,
  value: string,
): DatasetLayer;
export interface View {
  center: [number, number];
  zoom: number;
}
export interface SharedState {
  version: 1;
  baseUrl: string;
  profile: Profile;
  render: Render;
  sourceRevision: string;
  renderRevision: string;
  view: View;
  timeKey: string | null;
}
export function createState(
  input: Omit<SharedState, "version" | "renderRevision" | "timeKey"> & {
    timeKey?: string | null;
  },
): Promise<SharedState>;
export function encodeState(state: SharedState): string;
export function restoreState(
  fragment: string,
  expected: { baseUrl: string; profile: Profile; sourceRevision: string },
): Promise<SharedState>;
