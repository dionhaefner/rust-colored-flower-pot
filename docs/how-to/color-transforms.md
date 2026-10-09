# Apply color correction

The singleband and RGB endpoints accept a `color_transform` string. Apply the
same explicit stretch before comparing corrections so range changes do not
obscure the transform's effect.

```python
from urllib.parse import urlencode
import rcfp

query = urlencode(
    {"r": "red", "g": "green", "b": "blue", "color_transform": "gamma rgb 1.8"}
)
status, mime, image, _ = rcfp.request(
    "catalog.sqlite", "GET", "/rgb/lagoon/preview.png?" + query
)
assert status == 200
```

Examples include `gamma rgb 1.8`, `saturation 0.5`, and `sigmoidal rgb 10 0.5`.
Encode spaces and punctuation with a query builder. For singleband gamma use
`gamma 1 2.2`. RCFP evaluates color-operation formulas in its native rendering
pipeline. Consult the request specification for accepted operations.

```{image} ../_static/gamma.png
:alt: Lagoon RGB preview with gamma rgb 1.8, shown for comparison with the uncorrected tutorial RGB image.
:width: 360px
```

Run `python docs/examples/render_images.py catalog.sqlite images` to produce
corrected and uncorrected files with the same fixed ranges. Review masks and
categorical meaning before applying transforms to derived products.
