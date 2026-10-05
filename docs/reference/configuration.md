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

`DRIVER_PATH` identifies the catalog, while stored raster paths identify imagery.
`DEFAULT_TILE_SIZE` is a width/height pair. `RESAMPLING_METHOD` applies to the general reader's intermediate source
resampling. Direct native tiles use `REPROJECTION_METHOD` for their single final
sampling step, including aligned RasterIO windows; the general reader also uses
it for warping. Supported names are `nearest`,
`linear`, `cubic`, and `average`. Categorical explicit palettes force nearest.
`ALLOWED_ORIGINS_METADATA` and `ALLOWED_ORIGINS_TILES` govern separate HTTP groups.
`MAX_POST_METADATA_KEYS` bounds bulk metadata selection.

`SQL_USER`/`SQL_PASSWORD` supersede the deprecated MySQL/PostgreSQL-specific
aliases, which warn. Standard AWS metadata credentials and raster-specific
`RASTER_AWS_*` settings have different consumers. The remote snapshot directory
is under the platform's temporary directory; the legacy basename is intentional.

## Native environment controls

Worker/queue controls are environment-only. The direct-reader, fusion,
source/image retention and descriptor controls also have corresponding TOML
keys shown above; their environment variables use the `TC_` prefix.

| Variable                             | Default                                  | Accepted range/meaning                                                                 |
| ------------------------------------ | ---------------------------------------- | -------------------------------------------------------------------------------------- |
| `TC_RASTER_WORKERS`                  | 3, or 1 when multiprocessing is disabled | 1–64 workers per application                                                           |
| `TC_RASTER_QUEUE_CAPACITY`           | 8                                        | 1–1024 queued jobs                                                                     |
| `TC_RASTER_TIMEOUT_MS`               | 30000                                    | 1–3600000 ms response wait                                                             |
| `TC_RASTER_CACHE_BYTES`              | `TC_RASTER_CACHE_SIZE`, when unset       | 0–16 GiB uncompressed array budget; fallback default depends on captured core settings |
| `TC_REMOTE_RASTER_CACHE_TTL_SECONDS` | 60                                       | 0–86400 seconds                                                                        |
| `TC_SOURCE_BLOCK_CACHE_BYTES`        | 0                                        | Shared decoded source-block budget for opt-in fast paths                               |
| `TC_NATIVE_DIRECT_READ` | On in native mode | Direct output-grid tile reads using GDAL/PROJ; disabled by the Terracotta profile; false restores the general reader |
| `TC_REMOTE_SOURCE_CACHE_TTL_SECONDS` | 0 | 0–86400 seconds; worker-owned remote dataset reuse, at most 12 handles per worker |
| `TC_FUSED_RENDER` | On | Fuse stretch/packing when no color transform is requested; false restores the intermediate pipeline |
| `TC_RENDER_CACHE_BYTES` | 0 | 0–512 MiB encoded-image cache, including identical-request coalescing; at most 4096 retained images |
| `TC_REMOTE_RENDER_CACHE_TTL_SECONDS` | 0 | 0–86400 seconds; remote finished-image retention is disabled at zero |
| `TC_CATALOG_CACHE_BYTES` | 0 | 0–64 MiB SQLite dataset/metadata descriptor cache; at most 256 entries, invalidated through SQLite data_version |
| `TC_RUST_MERCATOR_FAST`              | Off                                      | Opt-in affine Web Mercator fast reads with fallback                                    |
| `TC_RUST_UTM_FAST`                   | Off                                      | Native-only UTM sampler; disabled in compatibility profile                             |
| `TC_PREFETCH_TILE_RING`              | Off                                      | Neighbor prefetch using idle workers and bounded caches                                |
| `TC_EXTRA_CMAP_FOLDER`               | Unset                                    | Custom `_rgba.npy` palette directory                                                   |
| `TC_GDAL_OPTIONS`                    | Unset                                    | JSON map of GDAL options applied by raster workers                                     |
| `AWS_ENDPOINT_URL_S3`                | Unset                                    | Metadata snapshot S3 endpoint                                                          |
| `AWS_XRAY_DAEMON_ADDRESS`            | Runtime default                          | Native X-Ray daemon destination                                                        |

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
in-flight work also consume memory. A zero image budget disables both retention
and image coalescing. A positive budget with zero remote image TTL coalesces
simultaneous remote requests without retaining completed responses.

Use immutable/versioned remote objects when enabling source or image retention.
Handle reopening does not clear GDAL's downloaded-range cache. A source-handle
TTL is not an ETag check or a guarantee of immediate remote mutation detection.
Worker settings are fixed for the application lifetime; refreshing credentials
through a provider depends on GDAL's provider behavior at source opening.

Bundled runtimes carry a CA certificate file for verified HTTPS. Set
`CURL_CA_BUNDLE` or the `CURL_CA_BUNDLE` entry in `TC_GDAL_OPTIONS` to use an
operator-managed trust store. Explicit GDAL options take precedence over the
bundled default. TLS verification remains enabled.
