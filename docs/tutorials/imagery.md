# Make RGB and computed images

You will combine three bands into a color preview, compute NDVI from red and NIR,
and compare categorical rendering. Start with the catalog from
[the catalog tutorial](catalog.md).

```sh
python docs/examples/render_images.py catalog.sqlite images
```

Open `images/rgb.png`. It assigns red, green, and blue datasets to the RGB
channels, each stretched from 0 to 3000. Open `images/gamma.png` to compare the
same image after gamma correction. Open `images/ndvi.png` for the expression
`(v1 - v2) / (v1 + v2)` with `v1=nir`, `v2=red`, a fixed −1 to 1 range,
and the viridis palette.

```{image} ../_static/rgb.png
:alt: RGB Sentinel-2 preview of the Venice lagoon using red, green, and blue bands.
:width: 360px
```

The WorldCover `images/classes.png` uses an explicit category-to-color mapping.
Its numeric labels are not stretched into a continuous gradient. The sample
script encodes structured query parameters with `urlencode` before making a
native request:

```{literalinclude} ../examples/render_images.py
:language: python
```

You have now used discovery, metadata, RGB, computation, and explicit palettes
without changing the source pixels. Read [rendering and fidelity](../explanation/rendering.md)
to understand masks and resampling, or use the [categorical](../how-to/categorical.md)
and [color-transform](../how-to/color-transforms.md) guides with your own imagery.
