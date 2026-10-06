"""Acquire the demo preview from the independent Terracotta 0.10.1 renderer."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import rasterio
import terracotta
from terracotta.server import create_app

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    assert terracotta.__version__ == "0.10.1"
    assert rasterio.__gdal_version__ == "3.10.3"
    row = next(
        row
        for row in json.loads((ROOT / "docs/examples/data/manifest.json").read_text())[
            "fixtures"
        ]
        if row["id"] == "red"
    )
    raster = ROOT / "docs/examples/data" / row["file"]
    assert hashlib.sha256(raster.read_bytes()).hexdigest() == row["sha256"]
    args.out.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temporary:
        database = str(Path(temporary) / "catalog.sqlite")
        driver = terracotta.get_driver(database)
        driver.create(["scene", "band"])
        driver.insert(["lagoon", "red"], str(raster))
        terracotta.update_settings(DRIVER_PATH=database)
        response = (
            create_app()
            .test_client()
            .get("/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D")
        )
        assert response.status_code == 200 and response.content_type == "image/png"
        (args.out / "preview.png").write_bytes(response.data)
    manifest = {
        "terracotta": terracotta.__version__,
        "gdal": rasterio.__gdal_version__,
        "source_sha256": row["sha256"],
        "preview_sha256": hashlib.sha256(response.data).hexdigest(),
        "attribution": row["attribution"],
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
