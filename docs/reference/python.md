# Native Python API

Select native mode before import, or leave `RCFP_COMPAT_PROFILE` unset. Native
operations raise `rcfp.RcfpError` on core failures. Direct HTTP operations return
a status/body even when the HTTP response represents an error. Check that status.

## Results and ownership

- `request` returns `(status, content_type, body_bytes, cacheable)`.
- `request_full` and `NativeApplication.request` add a response-header dictionary
  as a fifth item. Requests accept method, URL, request headers, and byte bodies.
- `render_preview` returns PNG bytes and raises on a non-success response.
- `compute_metadata` and `DataStore.get_metadata` return dictionaries. Bounds and
  range are tuples. `DataStore` registration accepts ordered key-value sequences.
- `NativeApplication(database, settings=None)` owns persistent workers and caches.
  The location can be a SQL catalog, STAC document or local source configuration.
  Reuse it. One-off request helpers create shorter-lived native ownership.
- `DataStore` calls commit individually. `batch` explicitly groups insert/delete
  operations and rolls back the group on failure.
- `rcfp.stac.SearchClient.search` and `rcfp.stac.Selection.from_items` return
  immutable asset selections with credential-free `metadata` and a content `id`.
  `Selection.render` returns PNG bytes. Use a context manager or `close` to drain
  workers and remove staged assets. See [STAC selection](../how-to/stac.md) for
  field mappings, provider limits, revision digests and refresh callbacks.

```python
import rcfp

store = rcfp.DataStore("catalog.sqlite")
store.batch(
    [
        {"operation": "insert", "keys": ["lagoon", "copy"], "raster": "/data/red.tif"},
        {"operation": "delete", "keys": ["lagoon", "copy"]},
    ]
)
```

The structured `data_operation` interface accepts a dictionary with `operation`,
`path`, optional `provider`, and operation-specific fields. Common catalog
operations are `keys`, `datasets`, `metadata`, `create`, `insert`, `delete`, and
`batch`. Dataset query fields include `where`, `limit`, and `page`. Where values
are lists. Raster metadata computation uses `operation="compute_metadata"`.
Prefer the typed convenience methods when they cover the task.

## Extracted signatures

```{include} ../_generated/python-native.md

```

WSGI application destruction drains and joins workers. Submitted GDAL work can
outlast its response deadline. See [WSGI hosting](../how-to/wsgi.md) and
[compatibility Python](compatibility-python.md) for the legacy facade.

Native multiband workflows use `DataStore.create(format="native")`, `insert`
with optional `band` and `values`, and `define` for persisted expressions.
`ingest` imports STAC snapshots. `get_keys`, `get_datasets`, `get_metadata` and
`get_raster_tile` read SQL, native keyed and direct STAC catalogs through the same
catalog adapter. Array reads support the existing masked-array/Future pattern.
See [logical datasets](layers.md) for registration, defaults and rollback.
The [advanced layer API](advanced-layers.md) remains available for explicit
overlap, category palettes and arbitrary grids.

`ComputeApplication` registers trusted Python scalar models over explicitly
selected native observations and shares raster workers and native image rendering.
`ComputeContext` describes the supplied arrays and dependency revisions. See
[Python compute layers](../how-to/python-compute.md) for registration, binary
array ownership, parameter schemas, caching and host lifecycle contracts.
