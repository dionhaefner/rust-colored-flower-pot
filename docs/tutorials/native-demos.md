# Try the native features

These examples use the installed RCFP package and its real offline samples.
The examples and captures contain modified Copernicus Sentinel data 2021.
Run each Python block with `python` from the same writable directory. Create the
sample once; use new catalog paths when repeating an example:

```sh
rcfp demo -o demo-data
```

## Logical bands and expressions

![Stored NDVI layer showing vegetation in green around the Venice lagoon.](../_static/native-demos/ndvi.png)

Store red/NIR bindings and publish NDVI as a discoverable dataset:

```python
from pathlib import Path
import rcfp

store = rcfp.DataStore("vegetation.sqlite")
store.create(["scene", "band"], format="native")
for band in ("red", "nir"):
    store.insert(["lagoon", band], f"demo-data/data/{band}.tif", values="scaled")
store.define(["lagoon", "ndvi"], "(nir-red)/(nir+red)",
             {"nir": ["lagoon", "nir"], "red": ["lagoon", "red"]},
             stretch=[-1, 1], colormap="viridis")
Path("ndvi.png").write_bytes(rcfp.render_preview("vegetation.sqlite", ["lagoon", "ndvi"]))
```

Open `ndvi.png`, or run `rcfp serve -d vegetation.sqlite` and
`rcfp connect localhost:5000 --local` in another terminal. Choose **lagoon / ndvi**.
See [logical datasets](../reference/layers.md) for packed multiband inputs.

## STAC sources

![Grayscale Sentinel-2 red band rendered from a STAC Item.](../_static/native-demos/stac.png)

The sample includes a local STAC Item with a pinned raster checksum. Render it
through the same application interface:

```python
from pathlib import Path
import json
import rcfp

app = rcfp.NativeApplication("demo-data/data/lagoon-stac.json")
status, _, body, _, _ = app.request("GET", "/datasets")
assert status == 200
print(json.loads(body))
status, _, image, _, _ = app.request(
    "GET", "/singleband/sentinel/2021-06-29T10:00:00Z/lagoon/red/preview.png")
assert status == 200
Path("stac.png").write_bytes(image)
del app
```

Open `stac.png`. To browse it, run
`rcfp serve --stac-item demo-data/data/lagoon-stac.json`, then connect the viewer.
[STAC guidance](../how-to/stac.md) covers static catalogs and API searches.
Direct STAC sources verify and stage the whole TIFF on first use.

## Indexed virtual mosaics

![Rendered overlap of two raster assets in the indexed virtual mosaic.](../_static/native-demos/mosaic.png)

Combine the overlapping red and green sample footprints in a bounded mosaic.
The overlap rule chooses the first valid observation. Inspect its contributors
and write a rendered image:

```python
from pathlib import Path
import rcfp

store = rcfp.LayerStore("mosaic.sqlite")
store.create()
store.put("lagoon", {
    "observations": [{"id": band, "assets": {"value": {
        "location": str(Path(f"demo-data/data/{band}.tif").resolve())}}}
        for band in ("red", "green")],
    "overlap": "first_valid",
    "mosaic": {"max_sources": 2, "max_reads": 2,
               "max_buffer_bytes": 16777216, "max_source_bytes": 16777216},
})
spec = {
    "bindings": {"v": {"asset": "value", "band": 1}}, "values": "raw",
    "channels": [{"expression": "v", "stretch": [0, 3000]}],
    "coverage": "any_valid", "encoding": "png",
    "grid": {"crs": "EPSG:3857", "bounds": [1371933, 5691890, 1380069, 5700155],
             "size": [256, 256], "resampling": "nearest"},
}
app = rcfp.LayerApplication("mosaic.sqlite")
print(app.plan("lagoon", spec))
values, contributors, owners = app.samples("lagoon", spec, provenance=True)
print(contributors)
Path("mosaic.png").write_bytes(app.render("lagoon", spec))
del app
```

Open `mosaic.png`. The plan selects two sources; valid pixels are owned by the
first observation where it covers the grid. [Mosaic guidance](../how-to/mosaics.md)
extends this to adjacent scenes, attribute selection and broad-view snapshots.

## Python compute layers

![Vegetation index returned by the NumPy callback and rendered as a scalar raster.](../_static/native-demos/model.png)

Run a NumPy model on aligned red/NIR inputs and render its returned scalar values.
This host uses a bounded worker pool and publishes the model through ordinary
dataset routes:

```python
from pathlib import Path
import numpy as np
import rcfp

store = rcfp.LayerStore("models.sqlite")
store.create()
store.put("satellite", {"observations": [{"id": "lagoon", "assets": {
    band: {"location": str(Path(f"demo-data/data/{band}.tif").resolve())}
    for band in ("red", "nir")}}], "overlap": "first_valid"})

def ndvi(values, valid, context):
    red, nir = values["red"], values["nir"]
    mask = valid["red"] & valid["nir"] & (nir + red != 0)
    result = np.zeros(red.shape)
    np.divide(nir - red, nir + red, out=result, where=mask)
    return result, mask

with rcfp.ComputeApplication("models.sqlite") as host:
    host.register("ndvi", ndvi, source="satellite", observation="lagoon", version="1",
        inputs={band: {"asset": band, "band": 1, "values": "raw"}
                for band in ("red", "nir")},
        preview_grid={"crs": "EPSG:3857", "bounds": [1371933, 5691890, 1380069, 5700155],
                      "size": [256, 256], "resampling": "nearest"},
        dependency_revisions={}, display={"stretch_range": [-1, 1], "colormap": "viridis"})
    status, _, image, _, _ = host.request("GET", "/singleband/ndvi/lagoon/value/preview.png")
    assert status == 200
    Path("model.png").write_bytes(image)
```

Open `model.png`. To serve a model to browsers, use the
[runnable WSGI host](../how-to/python-compute.md#register-an-observation-and-model).
Callbacks run in that trusted Python process; standalone `rcfp serve` does not
load Python functions.

## Browser SDK

![Leaflet SDK example displaying the red band over the Venice lagoon.](../_static/native-demos/leaflet.png)

Start the tile server and open the bundled viewer from a second terminal:

```sh
rcfp serve -d demo-data/map.sqlite
```

```sh
rcfp connect localhost:5000 --local
```

Open <http://127.0.0.1:5000/explore/examples/leaflet.html> and select **lagoon / red**.
The page's module imports `Client` and `leafletTileJSON` from the SDK served
beside it. Its complete HTML is available through the browser's **View source**.
Replace the URL's filename with `maplibre.html` or `openlayers.html` to try those
integrations. All three examples bundle their map libraries and work offline.
The [SDK reference](../reference/javascript.md) explains embedding and typed APIs.

## Rendered publication

![Browser snapshot loaded through a URL with a pinned render revision.](../_static/native-demos/publication.png)

Get an ETag and an immutable render revision, then revalidate and retrieve the
pinned representation using the byte-oriented API:

```python
from pathlib import Path
import rcfp

url = "/singleband/lagoon/red/preview.png"
app = rcfp.NativeApplication("demo-data/map.sqlite")
status, _, image, _, headers = app.request("GET", url)
assert status == 200
status, _, body, _, _ = app.request("GET", url, {"If-None-Match": headers["ETag"]})
assert status == 304 and body == b""
pinned = url + "?revision=" + headers["X-RCFP-Revision"]
status, _, same_image, _, pinned_headers = app.request("GET", pinned)
assert status == 200 and same_image == image
assert "immutable" in pinned_headers["Cache-Control"]
Path("published.png").write_bytes(same_image)
print(pinned, pinned_headers["Cache-Control"])
del app
```

Open `published.png`. [Rendered caching](../how-to/rendered-caching.md) shows the
equivalent HTTP requests and public proxy configuration. Keep the source snapshot
available for pinned URLs; RCFP does not archive old rasters.

## Native operations

![Native metrics reporting the configured raster workers, queue, and render cache.](../_static/native-demos/operations.png)

Set worker, queue and cache budgets, then inspect the effective configuration:

```sh
TC_RASTER_WORKERS=2 TC_RASTER_QUEUE_CAPACITY=8 TC_RENDER_CACHE_BYTES=4194304 \
  rcfp inspect -d demo-data/map.sqlite --json
```

Use the same settings to serve the map and expose the local metrics endpoint:

```sh
TC_EXPOSE_METRICS=true TC_RASTER_WORKERS=2 TC_RASTER_QUEUE_CAPACITY=8 \
  TC_RENDER_CACHE_BYTES=4194304 rcfp serve -d demo-data/map.sqlite
```

Open <http://127.0.0.1:5000/__terracotta/metrics> to see the worker, queue, and
cache counters. Stop the server with Ctrl+C before starting another example on
the same port. See [resource budgets](../how-to/resources.md)
and [monitoring](../reference/monitoring.md) for production configuration.
