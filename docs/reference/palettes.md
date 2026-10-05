# Palettes

Bundled continuous palette names are extracted from the installed core:

```{literalinclude} ../_generated/palettes.txt
:language: text
```

A continuous palette maps stretched intensities to RGBA. `colormap=explicit`
uses a category-to-color map and requires `explicit_color_map`; it selects nearest
sampling and suppresses continuous stretching. Hexadecimal RGB strings or RGBA
colors define category appearance. Encode mappings as JSON query values.

Custom continuous palette files have shape `(255, 4)`, dtype `uint8`, and filenames
ending `_rgba.npy`. `TC_EXTRA_CMAP_FOLDER` selects their directory before startup.
See [custom palettes](../how-to/palettes.md),
[categorical rendering](../how-to/categorical.md), and `/colormap` in
[the HTTP specification](http.md).
