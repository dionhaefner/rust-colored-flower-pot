# Rendering, COGs, and fidelity

A tile request selects a geographic region, reads source values/masks, reprojects
and samples them, stretches valid values, applies colors/transforms, and encodes
PNG bytes. The resulting image must preserve dataset identity, location,
coverage, transparency, and categorical meaning.

Cloud-optimized GeoTIFFs organize blocks and overviews so small tile requests
can read selected ranges instead of whole files. Overviews make zoomed-out
rendering cheaper. Reprojection remains necessary when the source grid differs
from the Web Mercator display grid; the presence of a COG alone does not remove
that work. Preparing new imagery and optimizing the serving algorithm are
separate choices.

Continuous data can use numerical or percentile stretch limits and color ramps.
Category values are labels and must use nearest sampling and explicit mappings.
Nodata/masks indicate invalid coverage rather than low intensity. An apparently
similar RGB image with a changed alpha mask can still be incorrect.

Compatibility compares decoded output semantics rather than compressed PNG
bytes. Small explained numerical differences can be acceptable at ordinary
application viewing scales, while wrong categories, seams, displaced imagery,
or lost transparency are material failures. Validate representative edge and
mask cases as well as fully opaque interior tiles.

Native tiles use a direct output grid through GDAL/PROJ by default. Contained
north-up Web Mercator windows use RasterIO; other geometries use one output-grid
warped VRT. Categorical rendering selects nearest; continuous rendering uses the
configured reprojection resampler. Coverage uses explicit positive alpha or GDAL
masks and excludes nodata/nonfinite samples. The general reader remains the
fallback for previews and non-map requests. The Terracotta profile selects that
reader. `TC_NATIVE_DIRECT_READ=false` restores it in native mode.
The opt-in UTM sampler uses a different sampling footprint and can change pixel
values; select native mode deliberately and review your imagery. Compatibility
mode disables that sampler. Web Mercator fast reads have their own applicability
checks. See [rendering recipes](../tutorials/imagery.md) and
[resource/performance guidance](performance.md).
