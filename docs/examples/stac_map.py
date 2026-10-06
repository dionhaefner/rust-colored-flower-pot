"""Render a pinned Item locally, or browse an existing POST STAC Search endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from rcfp.stac import SearchClient, Selection

DATA = Path(__file__).resolve().parent / "data"


def fixture_item():
    row = next(
        r
        for r in json.loads((DATA / "manifest.json").read_text())["fixtures"]
        if r["id"] == "red"
    )
    path = DATA / row["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    west, south, east, north = row["geographic_bounds"]
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "lagoon",
        "collection": "sentinel",
        "properties": {"datetime": "2021-06-29T10:00:00Z", "proj:epsg": 32632},
        "bbox": row["geographic_bounds"],
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        },
        "assets": {
            "red": {
                "href": path.as_uri(),
                "file:checksum": "1220" + row["sha256"],
                "raster:bands": [{"nodata": 0}],
            }
        },
        "links": [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", type=Path)
    parser.add_argument("--search", help="Full POST STAC API /search URL")
    parser.add_argument("--assets", nargs="+", default=["red"])
    parser.add_argument("--bbox", nargs=4, type=float, default=[12.3, 45.4, 12.4, 45.5])
    parser.add_argument(
        "--datetime", default="2021-06-29T00:00:00Z/2021-06-30T00:00:00Z"
    )
    parser.add_argument("--collections", nargs="+")
    parser.add_argument(
        "--revisions", type=Path, help="JSON collection/item/asset -> SHA-256 mapping"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.item and args.search:
        parser.error("choose --item or --search")
    revisions = json.loads(args.revisions.read_text()) if args.revisions else None
    if args.search:
        selection = SearchClient(args.search).search(
            assets=args.assets,
            bbox=args.bbox,
            datetime=args.datetime,
            collections=args.collections,
            revisions=revisions,
        )
    else:
        item = json.loads(args.item.read_text()) if args.item else fixture_item()
        base = args.item.resolve().as_uri() if args.item else None
        selection = Selection.from_items(
            [item], assets=args.assets, base_url=base, revisions=revisions
        )
    with selection:
        args.out.mkdir(parents=True, exist_ok=False)
        metadata = selection.metadata
        (args.out / "observations.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
        for index, asset in enumerate(metadata["assets"]):
            west, south, east, north = asset["bbox"]

            def mercator(lon, lat):
                lat = max(-85.05112878, min(85.05112878, lat))
                return 6378137 * math.radians(lon), 6378137 * math.log(
                    math.tan(math.pi / 4 + math.radians(lat) / 2)
                )

            left, bottom = mercator(west, south)
            right, top = mercator(east, north)
            png = selection.render(
                index, bounds_mercator=[left, bottom, right, top], range=[0, 2000]
            )
            (args.out / f"observation-{index}.png").write_bytes(png)
        print(f"Selection {selection.id}: {len(metadata['assets'])} selected assets")


if __name__ == "__main__":
    main()
