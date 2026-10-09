# Attribution and licenses

Domain explanations, ingestion workflows, categorical/color/palette recipes,
and migration context adapt material from Terracotta's documentation. Terracotta
is MIT licensed, copyright © 2018 DHI GRAS. The notice is reproduced below.
RCFP documentation reworks that material for the current native and compatibility
interfaces and the four documentation forms.

## Sample imagery

The supplied GeoTIFFs are small native pixel windows, without reprojection,
resampling, stretching, or replacement masks. The downloadable
[sample manifest](examples/data/manifest.json) records the source URLs, parent
and sample hashes, pixel window, CRS, transform, decoded values/masks, and attribution.
Tutorial preview images are rendered from those samples by the included scripts.

- Lagoon bands: contains modified Copernicus Sentinel data 2021.
- Category layer: ESA WorldCover 2021. See its source/attribution in the manifest.

Original imagery remains governed by its provider's terms. The application and
native bundle include their own third-party notices. Retain those with artifacts.

## Terracotta MIT notice

```{literalinclude} _static/TERRACOTTA-LICENSE.txt
:language: text
```
