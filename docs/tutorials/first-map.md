# Your first raster map

In this tutorial you will register real satellite imagery, start a tile server,
and open its image and browser client. You need an [installed RCFP wheel or source
checkout](../how-to/install.md), CPython 3.12–3.14, and a local web browser. Run
commands in a writable directory. No account, network download or cloud credentials
are needed after installation.

## Create a catalog and image

The installed executable supplies five small raster samples with checksums and
attribution, plus the catalog helper shown in the [catalog tutorial](catalog.md).
They contain Sentinel-2 bands from the Venice lagoon and an ESA WorldCover class
layer. Export them into a new directory and inspect one before creating a catalog:

```sh
rcfp sample -o sample
rcfp inspect --raster sample/data/red.tif --render-probe
```

Inspection lists the selected band, CRS, masks, block sizes, overview coverage
and effective configuration with its origin. These small samples have no
overviews; the warning explains the possible cost for larger sources and does
not prevent the tutorial. Inspection does not create a catalog. Keep `sample/data`
at its current location while using the catalog, whose records contain absolute
raster paths.

```sh
python sample/create_catalog.py map.sqlite
rcfp inspect -d map.sqlite
```

The script writes `map.sqlite` and `map.png` and prints red-band metadata. The
catalog has ordered keys `scene` and `band` and five datasets. Open `map.png`:
you should see a grayscale image of the lagoon. Its explicit range is 0–3000.
This operation creates a catalog; it does not copy the rasters into SQLite.

```{image} ../_static/red.png
:alt: Grayscale Sentinel-2 red-band preview of the Venice lagoon sample.
:width: 360px
```

## Serve it

```sh
rcfp serve -d map.sqlite
```

Leave that terminal running. In another terminal, visit
`http://127.0.0.1:5000/keys`. It returns the `scene` and `band` key definitions.
The server prints its selected port; substitute it below if 5000 is occupied.
Visit `http://127.0.0.1:5000/datasets` to see the registered datasets, then open
`http://127.0.0.1:5000/singleband/lagoon/red/preview.png?stretch_range=[0,3000]`.
The image should match `map.png`. Open `http://127.0.0.1:5000/apidoc` to explore
this server's HTTP contract.

## Explore in the client

```sh
rcfp connect localhost:5000
```

Select `lagoon` and the `red` band in the browser. The client fetches metadata and
imagery from the server you started. The bundled map and three SDK examples work offline with no
provider token. Use **Rendering** for RGB or an expression, **Inspect map center**
for display pixels, and **Create share link** to save the current view. See the
[JavaScript SDK reference](../reference/javascript.md) for embedding your own map
and supplying immutable source revisions. The imagery is centered around longitude 12.36°, latitude 45.46°.

Stop both processes with Ctrl+C when finished. Remove `map.sqlite` and `map.png`
only if you no longer want this tutorial catalog; keep the supplied raster files.
Continue with [building a catalog](catalog.md), or read
[rasters, keys, and metadata](../explanation/concepts.md).

For errors or planning ingestion from your own filenames, follow
[inspection and diagnostics](../how-to/diagnostics.md). The same catalog and
configuration concepts apply to [containers](../how-to/container.md),
[Python/WSGI](../how-to/wsgi.md) and [Lambda](../how-to/lambda.md).
