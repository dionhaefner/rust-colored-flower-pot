# Adopt native RCFP mode

Switch to native mode in staging first. Unset `RCFP_COMPAT_PROFILE` or set it to
`native`, then start new Python, CLI, and WSGI processes. Selecting native mode
does not rewrite rasters, upgrade a catalog, or change its format marker.

```sh
export RCFP_COMPAT_PROFILE=native
rcfp serve -d catalog.sqlite
```

Native mode reads and writes existing Terracotta 0.10 catalogs. Keep their files
and catalog locations while changing APIs. Native keyed datasets use a separate
`rcfp-native-2` SQLite database, with explicit [import and rollback](reference/layers.md#import-and-rollback).

## API changes

| Compatibility API                             | Native API and behavior                                                                                                                                                                              |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_driver(path)`                            | `DataStore(path, provider=...)` for catalog writes/metadata, or `data_operation` for structured native queries                                                                                       |
| `get_settings` / `update_settings`            | Supply settings to `NativeApplication` or WSGI `Application`. CLI/server configuration also uses `TC_*`                                                                                              |
| Driver connection contexts                    | Individual `DataStore` operations commit independently. `DataStore.batch` explicitly groups atomic insert/delete operations and rolls back on failure                                                |
| Driver raster arrays and asynchronous futures | Use `DataStore.get_raster_tile` for masked arrays and Futures, or native HTTP/request/render APIs for rendered bytes                                                                                 |
| `server.app:app`                              | `rcfp.wsgi.create_app(database)` for WSGI, or the native `rcfp serve` command for HTTP                                                                                                               |
| Named Terracotta exceptions                   | Native Python operations raise `RcfpError`. HTTP failures retain meaningful status codes and structured errors                                                                                       |
| Legacy CLI parser                             | Native CLI supports the core commands plus `data-operation`, `lambda-event`, `lambda-runtime`, and `serve --bind`. Both profiles use the Rust CLI. Python command extensions are unsupported         |
| Legacy rendering semantics                    | Native percentile labels and empty-tile dimensions follow the requested values. Reversed stretches are rejected. Zero/mask representation stays the same. The optional UTM sampler may change pixels |

For example, change a registration workflow from:

```python
import rcfp as tc

driver = tc.get_driver("catalog.sqlite")
driver.insert(["scene", "red"], "red.tif")
metadata = driver.get_metadata(["scene", "red"])
```

to:

```python
import rcfp

store = rcfp.DataStore("catalog.sqlite")
store.insert(["scene", "red"], "red.tif")
metadata = store.get_metadata(["scene", "red"])
```

For WSGI, change the entry point from `rcfp.server.app:app` to `rcfp.wsgi:app`
and provide `TC_DRIVER_PATH`, or create the app explicitly:

```python
from rcfp.wsgi import create_app

app = create_app("catalog.sqlite")
```

For direct rendering and catalog discovery:

```python
import rcfp

png = rcfp.render_preview("catalog.sqlite", ["scene", "red"])
status, content_type, body, cacheable = rcfp.request("catalog.sqlite", "GET", "/keys")
datasets = rcfp.data_operation(
    {
        "operation": "datasets",
        "path": "catalog.sqlite",
        "where": {"scene": ["scene"]},
        "limit": 10,
        "page": 0,
    }
)
```

## Native behavior corrections

Native CLI ingest requires the pattern's ordered key names to match the catalog
and commits the complete ingest as one transaction. A failed ingest restores
previous rows and metadata. A newly created catalog remains empty. This can hold
a database write transaction while raster metadata is computed. Individual Python
operations still commit independently, and `DataStore.batch` groups explicit
atomic writes. Inserting with `skip_metadata=True` invalidates previously stored
metadata so the next lazy fill reads the replacement raster.

Native percentile stretches use `p1`–`p99` for the stored 1st–99th percentiles,
`p0` for the minimum, and `p100` for the maximum. A compatibility caller using
`p0`–`p98` as array indices must add one to keep the same percentile in native
mode. For example, compatibility `["p1", "p98"]` becomes native `["p2", "p99"]`.
Native singleband/RGB/compute reject reversed ranges and allow equal endpoints
for constant data. MySQL default and partly defaulted HTTP stretches use numeric
endpoints. Compatibility Python Decimal representations are preserved.

Transparent out-of-bounds native tiles have the requested `tile_size`, ordered
as `[height, width]`, with status 200 and fully transparent alpha. Compatibility
continues returning its default dimensions. Zero-valued singleband pixels, RGB
black-key transparency, partial channel masks, and color-transform ordering keep
their existing representation.

New native metadata has correct entirely negative chunk maxima and affine
validity hulls for rotated grids in full, sampled, and chunked modes. Existing
stored metadata stays as it was. To adopt these corrections, back up the catalog
and reinsert the affected rasters with metadata computation enabled in native
mode. Sampled hulls still approximate sampled validity, and all hulls remain
convex rather than exact polygons of every valid region.

Native HTTP accepts a bare complete key name in `order_by`. Use `[scene,band]`
for multiple keys. Empty or unknown metadata columns produce a JSON 400 error.
Native `mod(a,b)` is an alias for the expression `%` operator, including its
dtype/mask behavior. The native specification advertises POST `/metadata`.
The Terracotta profile preserves its previous labels, statuses, sorting,
expression failure, and specification.

Native remote source handles have a 60-second lease by default. The named
Terracotta profile defaults to zero. Publish immutable/versioned raster URLs or
set `TC_REMOTE_SOURCE_CACHE_TTL_SECONDS=0` to disable handle reuse. Reopening does
not clear downloaded GDAL ranges or guarantee credential refresh. Identical
active requests share work even with zero finished-image cache. Completed images
are discarded at zero. See [resource tuning](how-to/resources.md).

Native GET/HEAD responses provide ETags and conditional responses. Mutable URLs
revalidate. Matching `revision` queries pin rendered images for immutable
caching. Publication remains private unless `HTTP_CACHE_PUBLIC=true`.
Authorization/cookie requests, errors and POST responses use `no-store`.
The named Terracotta profile retains its existing headers and behavior. See
[rendered delivery](how-to/rendered-caching.md) for freshness and proxy setup.

## Validation and rollout

1. Save the previous environment and configuration. Make a backend-appropriate
   catalog backup before a write trial or any explicit schema migration.
2. Run native mode against a staging copy with the original rasters. Verify
   discovery, metadata, dimensions, masks, categories, HTTP clients, and writes.
3. Benchmark representative cold/warm first views and navigation at realistic
   concurrency and memory budgets. Enable `TC_RUST_UTM_FAST=1` only after checking
   its output on your data. Native mode does not enable it automatically.
4. Change the application's imports, calls, error handling, and deployment entry
   points, then switch production after the staging checks pass.

To roll back while using the Terracotta 0.10 format, stop writers, restore the
previous profile/environment and application code, and restart. Terracotta
0.10.1 can also reopen that catalog. Restore a backup if a write trial changed
data you intended to keep. Avoid concurrent writers with different schema or
metadata encodings. Profile selection is not a database lock.

## Historical catalog migration

Both profiles support the historical 0.7/0.8/0.9 to 0.10 migration chain. This
is separate from adopting native mode. The 0.7 MySQL migration is unsupported. Use a separately supported export/import
workflow for that catalog. S3 snapshots cannot be migrated in place.

```sh
rcfp migrate older-catalog.sqlite --to 0.10.1 --dry-run
# Back up the catalog, inspect the printed SQL, then apply explicitly:
rcfp migrate older-catalog.sqlite --to 0.10.1 --yes
```

An unchanged catalog in a supported current format does not need this step.
SQLite/PostgreSQL migrations use transactions. MySQL DDL may commit part of a
failed migration. Restore the pre-migration backup before retrying a failed
MySQL migration. Downgrading after an explicit format upgrade requires restoring
the old catalog or a separately supported reverse migration.

Native multiband [keyed catalogs](reference/layers.md) use the separate
`rcfp-native-2` SQLite marker. They keep all definitions and metadata in the
database. Import uses explicit full-key bindings into a new database. The
original Terracotta catalog remains unchanged for rollback.
