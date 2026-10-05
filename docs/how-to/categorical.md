# Render categorical data

Category values identify labels, not continuous magnitudes. Keep the labels
intact during data preparation and select explicit colors when rendering.
Use the WorldCover example from [the catalog tutorial](../tutorials/catalog.md).

```python
import json
from urllib.parse import urlencode
import rcfp

query = urlencode(
    {
        "colormap": "explicit",
        "explicit_color_map": json.dumps(
            {
                "10": "006400",
                "30": "ffff4c",
                "40": "f096ff",
                "50": "fa0000",
                "80": "0064c8",
            }
        ),
    }
)
status, mime, image, _ = rcfp.request(
    "catalog.sqlite", "GET", "/singleband/worldcover/classes/preview.png?" + query
)
assert status == 200
with open("classes.png", "wb") as output:
    output.write(image)
```

Explicit mapping suppresses continuous stretching and selects nearest-neighbor
sampling, preserving labels. Only the listed classes receive colors in this
mapping; define all classes you need and check unmapped/nodata pixels. Colors
can be hexadecimal strings or RGBA tuples as described by the HTTP specification.

Attach a category legend with `extra_metadata` when computing metadata; the sample
catalog script demonstrates this. The legend is an application convention, not
an automatic classifier. Query `/metadata/worldcover/classes` to retrieve it.
When making overviews for categorical inputs, use nearest resampling. See
[rendering fidelity](../explanation/rendering.md) and
[the HTTP reference](../reference/http.md).
