"""Render RGB, NDVI, categorical, and color-corrected sample images."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

import rcfp


def render(database, output):
    output.mkdir(parents=True, exist_ok=True)
    app = rcfp.NativeApplication(database)
    requests = {
        "rgb": (
            "/rgb/lagoon/preview.png",
            {
                "r": "red",
                "g": "green",
                "b": "blue",
                "r_range": [0, 3000],
                "g_range": [0, 3000],
                "b_range": [0, 3000],
            },
        ),
        "ndvi": (
            "/compute/lagoon/preview.png",
            {
                "v1": "nir",
                "v2": "red",
                "expression": "(v1 - v2) / (v1 + v2)",
                "stretch_range": [-1, 1],
                "colormap": "viridis",
            },
        ),
        "classes": (
            "/singleband/worldcover/classes/preview.png",
            {
                "colormap": "explicit",
                "explicit_color_map": {
                    "10": "006400",
                    "30": "ffff4c",
                    "40": "f096ff",
                    "50": "fa0000",
                    "80": "0064c8",
                },
            },
        ),
        "gamma": (
            "/rgb/lagoon/preview.png",
            {
                "r": "red",
                "g": "green",
                "b": "blue",
                "r_range": [0, 3000],
                "g_range": [0, 3000],
                "b_range": [0, 3000],
                "color_transform": "gamma rgb 1.8",
            },
        ),
    }
    for name, (path, parameters) in requests.items():
        query = urlencode(
            {
                key: json.dumps(value) if isinstance(value, (dict, list)) else value
                for key, value in parameters.items()
            }
        )
        status, mime, body, _, _ = app.request("GET", f"{path}?{query}")
        if status != 200 or mime != "image/png":
            raise RuntimeError(f"{name}: {status}, {body!r}")
        (output / f"{name}.png").write_bytes(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.database, args.output)
