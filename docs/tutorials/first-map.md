# Your first raster map

In this tutorial you will register real satellite imagery, start a tile server,
and open its image and browser client. You need an [installed RCFP wheel or source
checkout](../how-to/install.md), CPython 3.12–3.14, and a local web browser. Run
commands in a writable directory. No account, network download or cloud credentials
are needed after installation.

## Create a catalog and image

The installed executable supplies five small rasters with checksums and
attribution. They contain Sentinel-2 bands from the Venice lagoon and an ESA
WorldCover class layer. Create a ready-to-serve catalog and preview:

```sh
rcfp demo -o demo-data
rcfp inspect -d demo-data/map.sqlite
```

Inspection lists bands, CRS, masks, block sizes, overview coverage and effective
configuration. These small samples have no overviews; that warning does not
prevent the tutorial. Keep `demo-data/data` at its current location because the
catalog contains absolute raster paths.

The catalog has ordered keys `scene` and `band` and five datasets. Open `demo-data/map.png`:
you should see a grayscale image of the lagoon. Its explicit range is 0–3000.
This operation creates a catalog. It does not copy the rasters into SQLite.

```{image} ../_static/red.png
:alt: Grayscale Sentinel-2 red-band preview of the Venice lagoon sample.
:width: 360px
```

## Serve it

```sh
rcfp serve -d demo-data/map.sqlite
```

Leave that terminal running. In another terminal, visit
`http://127.0.0.1:5000/keys`. It returns the `scene` and `band` key definitions.
The server prints its selected port. Substitute it below if 5000 is occupied.
Visit `http://127.0.0.1:5000/datasets` to see the registered datasets, then open
`http://127.0.0.1:5000/singleband/lagoon/red/preview.png?stretch_range=[0,3000]`.
The image should match `demo-data/map.png`. Open `http://127.0.0.1:5000/apidoc` to explore
this server's HTTP contract.

## Explore in the client

```sh
rcfp connect localhost:5000
```

Choose **lagoon / red** in **Layers**. To compose an RGB image, set **Display
mode** to **RGB composite** and assign the red, green, and blue bands. To try
NDVI, choose **Expression**, then **Insert NDVI** and **Apply expression**. Use
**Style** to adjust contrast, color, or opacity. Turn on **Inspect**, then click a
pixel to sample it. **Details** shows the metadata.

**World land**, the default basemap, uses bundled Natural Earth boundaries and
works offline. Choose **OpenStreetMap** in the basemap selector for online street
detail. The imagery is centered around longitude 12.36°, latitude 45.46°.

The `connect` command exits after opening the viewer. Stop the tile server with
Ctrl+C when finished.
Continue with [building a catalog](catalog.md), or read
[rasters, keys, and metadata](../explanation/concepts.md).

For errors or planning ingestion from your own filenames, follow
[inspection and diagnostics](../how-to/diagnostics.md). The same catalog and
configuration concepts apply to [containers](../how-to/container.md),
[Python/WSGI](../how-to/wsgi.md) and [Lambda](../how-to/lambda.md).
