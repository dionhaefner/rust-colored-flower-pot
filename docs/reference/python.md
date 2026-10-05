# Native Python API

Select native mode before import, or leave `RCFP_COMPAT_PROFILE` unset. Native
operations raise `rcfp.RcfpError` on core failures. Direct HTTP operations return
a status/body even when the HTTP response represents an error; check that status.

## Results and ownership

- `request` returns `(status, content_type, body_bytes, cacheable)`.
- `request_full` and `NativeApplication.request` add a response-header dictionary
  as a fifth item. Requests accept method, URL, request headers, and byte bodies.
- `render_preview` returns PNG bytes and raises on a non-success response.
- `compute_metadata` and `DataStore.get_metadata` return dictionaries; bounds and
  range are tuples. `DataStore` registration accepts ordered key-value sequences.
- `NativeApplication(database, settings=None)` owns persistent workers and caches.
  Reuse it. One-off request helpers create shorter-lived native ownership.
- `DataStore` calls commit individually. `batch` explicitly groups insert/delete
  operations and rolls back the group on failure.

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
`batch`. Dataset query fields include `where`, `limit`, and `page`; where values
are lists. Raster metadata computation uses `operation="compute_metadata"`.
Prefer the typed convenience methods when they cover the task.

## Extracted signatures

```{include} ../_generated/python-native.md

```

WSGI application destruction drains and joins workers; submitted GDAL work can
outlast its response deadline. See [WSGI hosting](../how-to/wsgi.md) and
[compatibility Python](compatibility-python.md) for the legacy facade.
