# Publish a Python compute layer

Use `rcfp.ComputeApplication` in a trusted Python host to publish a scalar model
as a map layer. RCFP aligns the selected raster bands, passes numeric arrays to
Python, and renders the returned values. The model does not need to write a
GeoTIFF or encode an image. This API requires the native profile and an installed
wheel with NumPy.

## Register an observation and model

Persist the input assets in a [native layer catalog](../reference/advanced-layers.md).
An observation identifies one coherent acquisition. Inputs in a callback always
come from that observation, even when another observation could supply a missing
band. The example uses separate satellite COGs; packed multiband files use the
same API with different one-based band selectors.

```python
import numpy as np
import rcfp
from rcfp.wsgi import Application

store = rcfp.LayerStore("observations.sqlite")
store.create()
store.put("satellite", {
    "observations": [{
        "id": "2021-06-29",
        "attributes": {"date": "2021-06-29", "crop": "wheat"},
        "assets": {
            "red": {"location": "/data/red.tif"},
            "nir": {"location": "/data/nir.tif"},
        },
    }],
    "overlap": "first_valid",
})

# Load an immutable weather snapshot in the host and declare its revision.
weather_stress = 0.25

def stress(values, valid, context):
    red, nir = values["red"], values["nir"]
    coverage = valid["red"] & valid["nir"] & (red + nir != 0)
    index = np.zeros(red.shape, dtype="float64")
    np.divide(nir - red, nir + red, out=index, where=coverage)
    return index * context.parameters["gain"] + weather_stress, coverage

host = rcfp.ComputeApplication("observations.sqlite", workers=2, queue_capacity=8)
host.register(
    "stress", stress,
    source="satellite", observation="2021-06-29", version="stress-model-1",
    inputs={
        "red": {"asset": "red", "band": 1, "values": "raw"},
        "nir": {"asset": "nir", "band": 1, "values": "raw"},
    },
    preview_grid={
        "crs": "EPSG:3857",
        "bounds": [1371933, 5691890, 1380069, 5700155],
        "size": [256, 256], "resampling": "nearest",
    },
    parameters={"gain": {"type": "number", "default": 1, "minimum": 0, "maximum": 4}},
    dependency_revisions={"weather": "weather-2021-06-29-r1", "crop": "wheat-r1"},
    display={"stretch_range": [-1, 2], "colormap": "viridis"},
)
app = Application(application=host)
```

Serve `app` using your Python WSGI host. Construct the application separately in
each host process after process creation; do not fork an application with live
native or Python worker threads. Call `host.close()` from your host's shutdown
hook, or use `ComputeApplication` as a context manager. See
[WSGI hosting](wsgi.md) for worker and graceful-shutdown configuration.

The [runnable host example](../examples/python_compute_host.py) accepts local
red/NIR paths, an explicit preview extent, a weather value and its revision. It
uses only installed public APIs and a threaded local WSGI server. Its stress
formula illustrates integration; it is not a qualified agronomic model.

## Discover and request the layer

`/keys` retains the catalog's `layer`, `observation`, `channel` keys. The model's
logical dataset has keys `["stress", "2021-06-29", "value"]`. Both `/datasets` and
`/tilesets` include registered models. Python registrations appear first; native
catalog results follow with their existing filtering and ordering. Pagination
applies to that combined sequence. Dataset filters and ordering apply within each
group. Layer IDs cannot collide with persisted layer IDs.

Use `/tilesets` to obtain the opaque ID and TileJSON URL. The host specification
at `/swagger.json` includes registered parameter declarations under
`x-rcfp-python-compute` and schemas for computed descriptors. Existing native SDK
`Client.tileJSON(keys, params)` and Leaflet, MapLibre and OpenLayers integrations
use these descriptors without a computation protocol. The regular `rcfp connect`
viewer also discovers the dataset. Web Mercator tiles use the ordinary
`/tiles/{id}/WebMercatorQuad/{z}/{x}/{y}.png` route or the keyed singleband route.
Set `PUBLIC_URL`, for example `https://maps.example.com/imagery`, for HTTPS or
a deployment prefix. Computed discovery and TileJSON reuse the native
[publication URL rules](../reference/native-services.md). Without this setting,
TileJSON uses HTTP and requires a `Host` header. Forwarded headers do not control
the published origin. For example:

```text
/singleband/stress/2021-06-29/value/preview.png?gain=2&stretch_range=[-1,3]
/singleband/stress/2021-06-29/value/13/4406/2927.png?gain=2&colormap=viridis
```

URL-encode query values. Numeric and boolean compute parameters are JSON scalars; string parameters use
ordinary decoded query strings. Each declaration requires `type` and
`default`; supported types are `number`, `integer`, `boolean` and `string`, with
optional numeric `minimum`/`maximum` and `enum`. Unknown or duplicate parameters
are rejected. HTTP requests cannot select assets, observations, functions or
executable code. To publish another date, register a layer bound to that explicit
observation, or replace the registration in trusted host code.

Tiles have a fixed 256 by 256 grid. Previews use the registered Web Mercator
extent and resampling; `tile_size=[height,width]` can change preview dimensions.
The preview extent also supplies TileJSON bounds, so choose it to cover the
layer. Each dimension is at most 2048 and the total grid at most 1,048,576 pixels.

`stretch_range` accepts two finite increasing numeric endpoints. `colormap`,
`explicit_color_map` and `color_transform` use the shared native scalar renderer,
including normalization, transparency and PNG encoding. Percentile stretches
are unavailable because a callback has no catalog-wide output statistics.
Changing display parameters does not change the compute identity. Metadata
reports the declared output range and defaults; it does not manufacture output
percentiles or summary statistics. Source-cell inspection and RGB/expression
composition of callback results are outside this scalar API.

## Array, validity and computation contracts

The synchronous callback receives `(values, valid, context)` and returns
`(output_values, output_valid)`. Both input mappings have the registered names.
Arrays are C-contiguous, little-endian float64 values and boolean validity masks
with shape `[height, width]`. `True` means valid. Inputs are read-only views over
Python-owned binary buffers; keeping a view alive also keeps its buffer alive.
Copy an input before modifying it. No pixel arrays cross the boundary as JSON.
The boundary copies buffers and does not promise zero-copy transfer.

RCFP reads each physical band onto the requested grid before Python executes.
Nodata, masks, alpha and nonfinite samples contribute to each band's independent
validity. Invalid numeric payloads have no defined value; consult the mask.
`values="raw"` preserves raw band samples. `values="scaled"` applies the inspected
`raw * scale + offset` after alignment. GDAL interpolation retains its existing
source-band precision, so an integer source can be quantized before float64
transfer. Integer samples above float64's exact integer range can lose precision.
Categorical inputs require raw values and nearest resampling.

`ComputeContext` contains immutable grid bounds/size/CRS/resampling, the selected
observation's ID and attributes, each input's physical band, calibration, units
and declared revision, the combined `source_revision`, validated compute
parameters and declared external dependency revisions. No implicit per-band
compositing occurs. The callback chooses how to combine independent input masks;
RCFP does not force an intersection after the callback. Output values must be a
real numeric two-dimensional array and output validity a boolean array of exactly
the supplied shape. Valid output values must be finite. Complex/object arrays,
broadcast masks and other shapes are rejected. Masked nonfinite values are allowed.
RCFP copies the output before retention and rendering, so later host mutations
cannot alter the cached result.

Computation is relative to the resampled request grid. The initial API supplies
no halo or padding. Spatial neighborhoods needing pixels beyond the supplied grid
are unsupported; a per-tile neighborhood may produce seams. Multiple-observation
compositing and computation before resampling are also outside this contract.

## Revisions and cache ownership

The host keeps three separate resources: native raster workers/caches, a computed
float64/validity cache, and an encoded-image cache. `compute_cache_bytes` defaults
to 64 MiB and `image_cache_bytes` to 16 MiB; both are bounded LRU byte budgets and
accept zero to disable retention. Identical concurrent computations share a
callback while declared dependencies permit caching. `compute_metrics()` reports
admitted work, callback counts and separate cache hits, misses, bytes and evictions.

Computed identity includes the layer ID, selected source and sidecar generations,
asset/band declarations and calibration, observation attributes, requested grid
and resampling, explicit compute version, validated parameters and external
dependency revisions. Encoded identity additionally includes display settings.
Change `version` when model code changes. Re-register with a new dependency
revision whenever weather, crop data or another external snapshot changes. Python
function identity does not determine correctness. `dependency_revisions={}`
explicitly declares no external dependencies; leaving it as `None` disables
computed/image retention and concurrent-result sharing. Raster caching still
follows the native source freshness policy.

Local raster and mask/overview/auxiliary changes invalidate identities. Source
changes during a read or callback cannot publish a computed result. For remote
inputs, immutable, revision-addressed raster locations and explicit asset
revisions are the host's responsibility. Unrevisioned remote sources disable
computed/image retention. An arbitrary revision string is not checksum verification
or an HTTP freshness protocol; changing the contents behind an unchanged remote
URL can also encounter GDAL's download cache. Use immutable locations for cached
models and update the catalog when publishing a new source revision.

Images return ETag and `X-RCFP-Revision`; matching `If-None-Match` returns 304.
A stale `revision` guard returns 409. Host responses use `Cache-Control: no-store`,
so model results do not become publicly cacheable through native rendered-cache
settings. Application cache budgets and registrations are process-local.

## Failure, admission and shutdown

`workers` bounds Python host pipelines and `queue_capacity` bounds admitted queued
pipelines. The host uses a separate bounded admission permit while raster reads
use the native executor's existing workers and queue. Python callbacks execute on
host threads after native reads return, never from native raster worker threads.
Threads do not guarantee parallel Python CPU execution; runtime GIL and model
library behavior still apply.

Invalid requests return 400, callback/output failures return a sanitized 500,
capacity exhaustion returns 503, and an elapsed host or native wait deadline
returns 504. Native raster admission can reject a host-admitted pipeline.
Registration validates input bindings and defaults before exposing a layer.
Callbacks must be synchronous and should raise ordinary exceptions for failure.

`timeout` bounds the request's wait for an admitted pipeline. Expired queued work
is discarded before source reads or callback invocation, while its permit remains
occupied until dequeued or removed during shutdown. Running Python/GDAL work
continues and remains accounted for. It may populate a computed cache after its
request expires. A deadline does not forcibly stop arbitrary Python or native
work, and a callback that holds the GIL can delay other Python threads' observation
of that deadline. `close()` stops admission, returns 503 to removed queued callers, joins running
pipelines, and releases the owned native session. It can wait indefinitely for a
callback that never returns. Calling back into the same compute host or closing
it from a callback is unsupported.

Only Python-hosted native applications run these callbacks. Standalone `rcfp serve`,
Rust integrations, Lambda entry points and the Terracotta profile retain their
existing behavior and do not load callbacks. GPU, asynchronous callbacks,
subprocess execution and cross-process cache sharing are unqualified. Platform
qualification results are recorded separately from prepared CI in maintainer notes.
