"""Check image, array, source-handle and SQLite cache freshness with real pixels."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import numpy as np
import rasterio
from navigation_benchmark import ROOT, digest, native, service, tile
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.binary, args.out = args.binary.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    source = ROOT / "tests/fixtures/corpus/red.tif"
    row = next(
        row
        for row in json.loads((source.parent / "manifest.json").read_text())["fixtures"]
        if row["id"] == "red"
    )
    assert digest(source) == row["sha256"]
    mutable = args.out / "mutable.tif"
    shutil.copyfile(source, mutable)
    env = dict(
        os.environ,
        RCFP_COMPAT_PROFILE="native",
        TC_NATIVE_DIRECT_READ="true",
        TC_FUSED_RENDER="true",
        TC_RENDER_CACHE_BYTES="1048576",
        TC_CATALOG_CACHE_BYTES="1048576",
        TC_EXPOSE_METRICS="true",
    )
    database = args.out / "catalog.sqlite"
    native(
        args.binary,
        args.out,
        env,
        {
            "operation": "driver_create",
            "path": str(database),
            "keys": ["scene", "band"],
        },
    )
    metadata = native(
        args.binary,
        args.out,
        env,
        {
            "operation": "compute_metadata",
            "path": str(mutable),
            "options": {"max_shape": [512, 512]},
        },
    )
    native(
        args.binary,
        args.out,
        env,
        {
            "operation": "insert",
            "path": str(database),
            "keys": ["mutable", "red"],
            "raster": str(mutable),
            "metadata": metadata,
        },
    )
    west, south, east, north = metadata["bounds"]
    x, y = tile((west + east) / 2, (south + north) / 2, 14)
    route = f"/singleband/mutable/red/14/{x}/{y}.png"
    checks = []
    with service(args.binary, database, env, args.out, []) as (origin, _process):

        def image(query=""):
            with urlopen(origin + route + query, timeout=30) as response:
                return np.asarray(
                    Image.open(io.BytesIO(response.read())).convert("RGBA")
                )

        def metrics():
            return json.load(urlopen(origin + "/__terracotta/metrics"))

        first = image()
        before = metrics()
        assert np.any(first[:, :, 3])
        assert np.array_equal(first, image())
        after = metrics()
        assert after["render_cache"]["hits"] > before["render_cache"]["hits"]
        assert after["submitted"] == before["submitted"]
        checks.append("finished-image hit bypasses native execution")
        assert image("?tile_size=%5B128%2C128%5D").shape == (128, 128, 4)
        checks.append("dimensions participate in cache identity")
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE metadata SET max=max*2")
        revised = image()
        assert not np.array_equal(first, revised)
        checks.append("external SQLite update invalidates descriptors and stretches")
        with (
            rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False),
            rasterio.open(mutable, "r+") as dataset,
        ):
            dataset.write_mask(np.zeros((dataset.height, dataset.width), dtype="uint8"))
        assert Path(str(mutable) + ".msk").is_file()
        masked = image()
        assert not np.any(masked[:, :, 3])
        checks.append("external mask creation invalidates source, arrays and images")
        with rasterio.open(mutable, "r+") as dataset:
            dataset.write_mask(
                np.full((dataset.height, dataset.width), 255, dtype="uint8")
            )
        assert np.any(image()[:, :, 3])
        checks.append("external mask replacement invalidates cached coverage")
        with rasterio.open(mutable, "r+") as dataset:
            values = dataset.read(1)
            values[values != 0] = np.minimum(
                values[values != 0].astype("uint32") * 2, 65535
            )
            dataset.write(values, 1)
        assert not np.array_equal(image(), revised)
        checks.append("main raster mutation invalidates all local caches")
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM metadata")
            connection.execute("DELETE FROM datasets")
        try:
            image()
        except HTTPError as error:
            assert getattr(error, "code", None) == 404, error
        else:
            raise AssertionError("deleted dataset survived in catalog cache")
        checks.append("catalog deletion cannot return a retained image")
    (args.out / "report.json").write_text(
        json.dumps({"binary_sha256": digest(args.binary), "checks": checks}, indent=2)
        + "\n"
    )
    print(f"Passed {len(checks)} cache freshness checks")


if __name__ == "__main__":
    main()
