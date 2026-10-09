# Publish cacheable rendered images

Native HTTP, `NativeApplication`, WSGI and Lambda share a delivery policy.
Successful GET and HEAD responses have a strong `ETag`. Mutable URLs use
`Cache-Control: private, no-cache`, so a browser may retain a response but must
validate it before reuse. Send `If-None-Match` with the previous ETag to receive
a bodyless 304 when the current representation matches. Weak validators and
lists are accepted for this comparison. `If-Match` uses strong comparison and
returns 412 on a mismatch. HEAD returns the GET representation's length. 304
responses omit `Content-Length`.

Errors, POST responses, OPTIONS responses, metrics, and requests carrying
`Authorization` or `Cookie` use `no-store` without validators. Successful
transparent tiles and empty discovery results follow the ordinary GET policy.
Errors are not retained in the encoded-result cache. The
`terracotta-0.10.1` profile keeps its existing `no-cache` headers and ignores
conditional headers.

## Pin a rendered revision

Singleband, RGB, compute and native matrix tiles return `X-RCFP-Revision`, an opaque
SHA-256 digest of the render identity and encoded representation. It is distinct
from an asset checksum or a STAC selection revision. Each tile, output size and
style has its own render revision.

Fetch the mutable URL, then append the returned digest as `revision`:

```python
from urllib.parse import urlencode
from urllib.request import Request, urlopen

url = "http://localhost:5000/singleband/scene/red/preview.png"
with urlopen(Request(url, method="HEAD")) as response:
    revision = response.headers["X-RCFP-Revision"]
published_url = url + "?" + urlencode({"revision": revision})
```

A matching revision gets `private, max-age=31536000, immutable`. The origin
resolves the current selection and checks the revision before evaluating
conditions. A changed revision returns 409 with `no-store`. A deleted dataset
returns 404. An immutable URL cannot return a different encoded representation.
The guard does not archive sources, so preserve the source/catalog snapshot when old
URLs must remain available. A browser or proxy can continue serving an already
cached immutable representation after the origin's selection changes. Publish a
new URL to show the new selection. Invalidating an old URL requires the cache
operator's purge mechanism.

Registered layers have an equivalent GET route,
`/layers/{id}/render.png`, using the persisted default render. Supply a
percent-encoded JSON `spec` query value for an explicit render specification.
The existing POST `/layers/{id}/render` remains available and uses `no-store`.

Use mutable URLs for selections such as “latest acquisition.” Resolve such a
selection first and pin each resulting image to publish a snapshot. Do not
reuse a revision parameter after changing bands, expressions, ranges or size.
ETags include selected metadata/defaults, source generations, logical
band/calibration/validity declarations, expressions, overlap/coverage policy,
grid, rendering settings and encoded bytes. Query order and JSON whitespace
do not change identity. Unknown ignored rendering query options are excluded.

## Control source freshness

Local main, mask, overview and auxiliary file stamps invalidate arrays and
rendered images. Unix stamps also include inode and change time, detecting
replacement and writes that preserve modification time. Other platforms retain
size/mtime detection. These are filesystem stamps, not full-file checksums.
Use new source paths for published revisions, VRT dependencies, or files whose
stamps can be restored. Publish catalog changes transactionally and avoid
modifying source files during reads. Detected mid-render stamp changes return
409 and are not retained.

Catalog descriptors are refreshed before an encoded-image lookup. Stored
logical plans include the generations of their input files. STAC staging
verifies pinned asset checksums. Expiring signed access locations are separate
from an explicitly revisioned asset identity. Private transport locations remain
part of the application cache key, while public identifiers expose only digests.

Unversioned remote URLs retain the configured source, array and image TTLs.
A conditional request validates the representation visible to the application.
It does not force an upstream ETag check or clear GDAL's downloaded ranges.
Immutable remote objects, explicit asset revisions and checksum-pinned STAC
selections are the publication inputs. Credential rotation and immediate remote
mutation detection need provider-specific verification. See
[remote data](remote-data.md) and [cache resource limits](resources.md).

## Enable a shared proxy or CDN

For a catalog whose anonymous responses are intended for public distribution:

```sh
export TC_HTTP_CACHE_PUBLIC=true
export TC_RENDER_CACHE_BYTES=16777216
rcfp serve -d catalog.sqlite --bind 127.0.0.1:5000
```

`HTTP_CACHE_PUBLIC` is a Boolean TOML/core setting, defaulting to false.
It changes eligible GET/HEAD policies to `public, no-cache` or
`public, max-age=31536000, immutable`. Authorization/cookie requests still use
`no-store`. The application has one captured source/credential scope and its own
caches. Construct separate applications for distinct source access scopes.
RCFP does not authenticate incoming requests. Apply access control at your
deployment boundary and keep private catalogs private.

The [nginx configuration](../examples/render-cache.nginx.conf) honors the
origin's cache policy, bypasses authorization/cookie requests, retains the full
query string in the cache key and locks duplicate fills. Replace `@WORK@` with
an absolute writable directory, `@PORT@` with the proxy port, `@ORIGIN@` with
the RCFP origin and `@EXAMPLE@` with the absolute path to the
[browser example](../examples/rendered-cache.html). Create `cache`, `body`,
`temp`, `fastcgi`, `uwsgi` and `scgi` below the work directory, then run:

```sh
nginx -p /absolute/work/ -c /absolute/work/nginx.conf
```

Open `/example`, enter a render URL and select **Load snapshot**. The example
resolves a revision with HEAD and displays the pinned image. Cross-origin use
requires an allowed tile origin. RCFP exposes ETag and revision headers to
allowed origins and sends `Vary: Origin, Authorization, Cookie`, including for
denied origins. Keep the full URL, query and Vary fields in CDN cache identity.
Preserve origin `Cache-Control`, ETag and error status. Disable forced caching
and stale-error overrides for mutable URLs. nginx's
[proxy cache documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
describes the corresponding deployment controls.

## Measure reuse

The bounded encoded cache covers singleband, RGB, compute and registered layer
renders. `TC_RENDER_CACHE_BYTES` defaults to zero and allows up to 512 MiB and
4096 entries. `TC_REMOTE_RENDER_CACHE_TTL_SECONDS=0` disables finished remote
image retention. Identical active renders coalesce at every budget. Successful
cache hits avoid raster execution and encoding. Conditional hits also avoid
response payload transfer. A cache miss can still require rendering before its
validator is known.

Use [metrics](../reference/monitoring.md) to inspect `render_cache` hits,
misses, coalescing, retained bytes and raster submissions. Measure full first
viewports and revisits, RSS, source I/O, response bytes and duplicate requests
under the same worker and cache budgets. The maintained
`tests/rendered_cache_contracts.py` runner records three alternating paired
trials on real samples and optionally verifies nginx and Chromium. Loopback
measurements qualify that workload, rather than WAN/CDN or cloud performance.
