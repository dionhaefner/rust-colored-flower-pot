"""Register the supplied raster samples and write a first preview."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import rcfp

DATA = Path(__file__).resolve().parent / "data"


def verify_data():
    manifest = json.loads((DATA / "manifest.json").read_text())
    for row in manifest["fixtures"]:
        if (
            hashlib.sha256((DATA / row["file"]).read_bytes()).hexdigest()
            != row["sha256"]
        ):
            raise RuntimeError(f"Sample checksum mismatch: {row['file']}")


def create_catalog(database: Path):
    verify_data()
    if database.exists():
        raise FileExistsError(f"Choose a new catalog path: {database}")
    database.parent.mkdir(parents=True, exist_ok=True)
    store = rcfp.DataStore(database)
    store.create(
        ["scene", "band"],
        key_descriptions={"scene": "Sample scene", "band": "Raster band"},
    )
    for band in ("red", "green", "blue", "nir"):
        store.insert(["lagoon", band], DATA / f"{band}.tif")
    metadata = rcfp.compute_metadata(
        DATA / "classes.tif",
        extra_metadata={
            "categories": {
                "trees": 10,
                "grassland": 30,
                "cropland": 40,
                "built": 50,
                "water": 80,
            }
        },
    )
    store.insert(["worldcover", "classes"], DATA / "classes.tif", metadata=metadata)
    png = rcfp.render_preview(database, ["lagoon", "red"], stretch_range=[0, 3000])
    database.with_suffix(".png").write_bytes(png)
    return store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    store = create_catalog(args.database.resolve())
    print(json.dumps(store.get_metadata(["lagoon", "red"]), indent=2))


if __name__ == "__main__":
    main()
