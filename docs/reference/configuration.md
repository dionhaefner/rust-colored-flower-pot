# Configuration

Legacy/core environment variables use `TC_` plus the TOML setting name below.
Explicit settings override environment values. CLI global `-c/--config` loads
TOML; explicit CLI flags override it. Unknown keys/invalid values fail at startup.
Boolean environment values accept true/false and common 1/0 equivalents; arrays
use JSON. Settings snapshots belong to an application/driver lifetime.

## Profile selection

`--compat-profile` > TOML `COMPAT_PROFILE` > `RCFP_COMPAT_PROFILE` > `native`.
The accepted names are `native` and `terracotta-0.10.1`. Python freezes its
selection on first import; construct a new process to change it. `COMPAT_PROFILE`
is a selector, not a field in the legacy `TerracottaSettings` named tuple.

## Core defaults

Extracted from the installed native core:

```{include} ../_generated/settings.md

```

`DRIVER_PATH` identifies a catalog location or local source configuration, while
stored raster paths identify imagery. Native hosting accepts SQL and
[STAC sources](../how-to/stac.md) through the same application entry points.
`DEFAULT_TILE_SIZE` is a `[height, width]` pair. `RESAMPLING_METHOD` applies to the general reader's intermediate source
resampling. Direct native tiles use `REPROJECTION_METHOD` for their single final
sampling step, including aligned RasterIO windows; the general reader also uses
it for warping. Supported names are `nearest`,
`linear`, `cubic`, and `average`. Categorical explicit palettes force nearest.
`ALLOWED_ORIGINS_METADATA` and `ALLOWED_ORIGINS_TILES` govern separate HTTP groups.
`MAX_POST_METADATA_KEYS` bounds bulk metadata selection.

`HTTP_CACHE_PUBLIC` defaults to false. Set it to true only for anonymously
published catalogs to permit shared caching. The [rendered caching guide](../how-to/rendered-caching.md)
defines validators, revision-pinned URLs, access scopes and source freshness.

`SQL_USER`/`SQL_PASSWORD` supersede the deprecated MySQL/PostgreSQL-specific
aliases, which warn. Standard AWS metadata credentials and raster-specific
`RASTER_AWS_*` settings have different consumers. The remote snapshot directory
is under the platform's temporary directory; the legacy basename is intentional.

## Native environment controls

Worker/queue controls are environment-only. The direct-reader, fusion,
source/image retention and descriptor controls also have corresponding TOML
keys shown above; their environment variables use the `TC_` prefix.

| Variable                             | Default                                  | Accepted range/meaning                                                                                                   |
| ------------------------------------ | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `TC_RASTER_WORKERS`                  | 3, or 1 when multiprocessing is disabled | 1–64 workers per application                                                                                             |
| `TC_RASTER_QUEUE_CAPACITY`           | 8                                        | 1–1024 queued jobs                                                                                                       |
| `TC_RASTER_TIMEOUT_MS`               | 30000                                    | 1–3600000 ms response wait                                                                                               |
| `TC_RASTER_CACHE_BYTES`              | `TC_RASTER_CACHE_SIZE`, when unset       | 0–16 GiB uncompressed array budget; fallback default depends on captured core settings                                   |
| `TC_REMOTE_RASTER_CACHE_TTL_SECONDS` | 60                                       | 0–86400 seconds                                                                                                          |
| `TC_SOURCE_BLOCK_CACHE_BYTES`        | 0                                        | Shared decoded source-block budget for opt-in fast paths                                                                 |
| `TC_NATIVE_DIRECT_READ`              | On in native mode                        | Direct output-grid tile reads using GDAL/PROJ; disabled by the Terracotta profile; false restores the general reader     |
| `TC_ASYNC_TIFF`                      | On in native mode                        | Exact TIFF headers and eligible stored-pixel reads; false uses GDAL; disabled by the Terracotta profile                  |
| `TC_REMOTE_SOURCE_CACHE_TTL_SECONDS` | 60 native; 0 Terracotta profile          | 0–86400 seconds; at most 12 worker-owned remote source leases per worker                                                 |
| `TC_NATIVE_WARP_PLAN`                | On                                       | Reuse native affine warp plans; false restores per-tile utility setup; requires direct native reads                      |
| `TC_COMPACT_RASTER_CACHE`            | Off                                      | Lossless typed samples and packed masks with a bounded f64 hot tier inside the existing array budget                     |
| `TC_SIMD_RENDER`                     | On                                       | Exact AVX2 normalization when supported, otherwise scalar; requires fused rendering                                      |
| `TC_PNG_LIBDEFLATE`                  | On                                       | Optional bundled lossless compressor for PNG levels other than 1; unavailable libraries fall back to the Rust compressor |
| `TC_FUSED_RENDER`                    | On                                       | Fuse stretch/packing when no color transform is requested; false restores the intermediate pipeline                      |
| `TC_RENDER_CACHE_BYTES`              | 0                                        | 0–512 MiB finished-image retention; at most 4096 retained images; active identical requests coalesce even at zero        |
| `TC_REMOTE_RENDER_CACHE_TTL_SECONDS` | 0                                        | 0–86400 seconds; remote finished-image retention is disabled at zero                                                     |
| `TC_CATALOG_CACHE_BYTES`             | 0                                        | 0–64 MiB SQLite dataset/metadata descriptor cache; at most 256 entries, invalidated through SQLite data_version          |
| `TC_RUST_MERCATOR_FAST`              | Off                                      | Opt-in affine Web Mercator fast reads with fallback                                                                      |
| `TC_RUST_UTM_FAST`                   | Off                                      | Native-only UTM sampler; disabled in compatibility profile                                                               |
| `TC_PREFETCH_TILE_RING`              | Off                                      | Neighbor prefetch using idle workers and bounded caches                                                                  |
| `TC_EXTRA_CMAP_FOLDER`               | Unset                                    | Custom `_rgba.npy` palette directory                                                                                     |
| `TC_GDAL_OPTIONS`                    | Unset                                    | JSON map of GDAL options applied by raster workers                                                                       |
| `AWS_ENDPOINT_URL_S3`                | Unset                                    | Metadata snapshot S3 endpoint                                                                                            |
| `AWS_XRAY_DAEMON_ADDRESS`            | Runtime default                          | Native X-Ray daemon destination                                                                                          |

`TC_EXPOSE_METRICS=true` corresponds to `EXPOSE_METRICS`; metrics are hidden by
default. Native caches differ from Terracotta's compressed LFU cache, so equal
numeric budgets are not equal retained contents.

## Build and runtime paths

| Variable                  | Purpose                                              |
| ------------------------- | ---------------------------------------------------- |
| `RCFP_GDAL_CACHE`         | Reuse checksum-verified downloaded native archives   |
| `RCFP_GDAL_PREFIX`        | Use an existing GDAL/PROJ build prefix               |
| `RCFP_CARGO_OFFLINE`      | Set to `1` for cached Cargo dependencies             |
| `RCFP_BUNDLE_DIR`         | Reuse a prepared native bundle when building a wheel |
| `RCFP_NATIVE_TARGET`      | Reuse a native Cargo target directory                |
| `TERRACOTTA_RUNTIME_ROOT` | Explicit native bundle location                      |
| `TERRACOTTA_GDAL_LIBRARY` | Explicit external GDAL shared library                |
| `GDAL_DATA` / `PROJ_DATA` | Native data lookup directories                       |

Legacy runtime-variable names remain supported. An external GDAL library needs
its dependencies and data; a matching wheel configures its private runtime.

Raster, rendered-image, and descriptor cache budgets are separate. Retained image
budgets count allocated payload capacity and estimated keys; descriptor budgets
are logical estimates. Allocator, GDAL handles, downloaded ranges, stacks, and
in-flight work also consume memory. A zero image budget disables finished-image retention. Identical active renders
share work at every budget, including zero; completed images are discarded when
retention is disabled. Zero remote image TTL also disables remote finished-image
retention. Active flights are bounded by HTTP admission.

Native mode retains remote source handles for 60 seconds by default; the named
Terracotta profile defaults to zero. Explicit zero disables remote handle reuse.
Use immutable/versioned remote objects with source or image retention.
Handle reopening does not clear GDAL's downloaded-range cache. A source-handle
TTL is not an ETag check or a guarantee of immediate remote mutation detection.
Worker settings are fixed for the application lifetime; refreshing credentials
through a provider depends on GDAL's provider behavior at source opening.

Async-TIFF access applies to eligible local TIFFs and anonymous HTTP ranges.
It reads headers and exact stored pixels on contained, north-up EPSG:3857 grids.
Layer reads require nearest sampling of base pixels and finite or absent nodata.
Statistics and detailed asset metadata use GDAL. It retains up to 12 headers per worker in a
4 MiB logical budget shared with compressed remote ranges and uses the source
lease TTL for remote headers. Remote pixels require a strong ETag and
version-consistent responses. Unsupported
layouts/codecs, sidecars, authenticated paths and explicit GDAL options fall
back to GDAL. Custom HTTPS trust settings also use GDAL. Disable it with
`TC_ASYNC_TIFF=false` when comparing behavior or diagnosing source access.

Reusable native warp plans apply to eligible affine scalar sources; GCP/RPC/
geolocation, compound/three-axis CRS and mixed-type sources use the general GDAL utility.
Each source retains one base transformer and at most four overview plans, with
worker ownership and the source lease lifetime. These overview plans can open
additional dataset handles; they use the same GDAL block-cache budget. Compact
array storage preserves all f64 bits, hidden samples and masks; unsupported values
retain full storage. Measure it before enabling: extra conversion CPU can hurt
fresh pans even when more revisits fit in memory.

Bundled runtimes carry a CA certificate file for verified HTTPS. Set
`CURL_CA_BUNDLE` or the `CURL_CA_BUNDLE` entry in `TC_GDAL_OPTIONS` to use an
operator-managed trust store. Explicit GDAL options take precedence over the
bundled default. TLS verification remains enabled.

Native-only `PUBLIC_URL` (environment `TC_PUBLIC_URL`) sets the absolute public
HTTP(S) base for TileJSON, including any deployment prefix. It defaults to an
empty string, which uses plain HTTP and `Host`. See
[native map services](native-services.md) for proxy and socketless requests.
