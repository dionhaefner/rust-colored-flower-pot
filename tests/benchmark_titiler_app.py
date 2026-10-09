"""Resolve the shared SQLite benchmark catalog as TiTiler STAC assets.

Raster reads, expressions, stretching and PNG encoding use TiTiler/rio-tiler.
The adapter adds catalog lookup and the other servers' transparent-empty policy.
"""

from __future__ import annotations

import datetime
import io
import os
import sqlite3

import attrs
import pystac
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from PIL import Image
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import STACReader
from rio_tiler.profiles import img_profiles
from titiler.core.factory import MultiBaseTilerFactory
from titiler.core.utils import render_image


def catalog_item(scene: str) -> pystac.Item:
    with sqlite3.connect(os.environ["TC_DRIVER_PATH"]) as db:
        rows = db.execute(
            "SELECT d.band, d.path, m.bounds_north, m.bounds_east, "
            "m.bounds_south, m.bounds_west FROM datasets d JOIN metadata m "
            "ON d.scene=m.scene AND d.band=m.band WHERE d.scene=? ORDER BY d.band",
            (scene,),
        ).fetchall()
    if not rows:
        raise HTTPException(404, "Unknown scene")
    west, south, east, north = rows[0][2:]
    item = pystac.Item(
        id=scene,
        geometry={
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
        bbox=[west, south, east, north],
        datetime=datetime.datetime(2021, 1, 1, tzinfo=datetime.UTC),
        properties={},
    )
    for band, path, *_ in rows:
        item.add_asset(band, pystac.Asset(path, media_type=pystac.MediaType.COG))
    return item


@attrs.define
class CatalogReader(STACReader):
    def __attrs_post_init__(self):
        self.item = self.input
        super().__attrs_post_init__()


app = FastAPI()
# All three services use the same explicit compression level.
img_profiles["png"] = {"zlevel": 1}


def render_catalog_image(image, rescale=None, **kwargs):
    # Terracotta reserves zero for masked values and maps valid values to 1..255.
    # rio-tiler's integer truncation differs
    # by up to one display level from Terracotta's rounding.
    if rescale:
        image.rescale(rescale, out_range=((1, 255),))
    return render_image(image, **kwargs)


factory = MultiBaseTilerFactory(
    reader=CatalogReader, path_dependency=catalog_item, render_func=render_catalog_image
)
app.include_router(factory.router)


@app.get("/health")
def health():
    return {"ready": True, "pid": os.getpid()}


@app.exception_handler(TileOutsideBounds)
async def empty_tile(_request, _exception):
    output = io.BytesIO()
    Image.new("RGBA", (256, 256)).save(output, format="PNG", compress_level=1)
    return Response(output.getvalue(), media_type="image/png")
