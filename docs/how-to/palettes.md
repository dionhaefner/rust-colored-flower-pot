# Add a custom palette

Create an NPY array with 255 rows, four RGBA channels, and unsigned 8-bit values.
The filename must end in `_rgba.npy`. This example produces a grayscale ramp:

```sh
python docs/examples/custom_palette.py palettes
```

```{literalinclude} ../examples/custom_palette.py
:language: python
```

Set the folder before starting RCFP so palette discovery sees the file:

```sh
export TC_EXTRA_CMAP_FOLDER="$PWD/palettes"
rcfp serve -d catalog.sqlite
```

PowerShell: `$env:TC_EXTRA_CMAP_FOLDER = (Resolve-Path palettes).Path`.
Request `/singleband/lagoon/red/preview.png?colormap=graycustom&stretch_range=[0,3000]`.
A 200 PNG response verifies the palette loaded. An unknown palette returns an
error; check the suffix, dtype, shape, and startup environment.

Use [explicit category maps](categorical.md) for labels. Custom continuous
palettes color stretched values; they do not define category semantics. The
[palette reference](../reference/palettes.md) lists bundled names.
