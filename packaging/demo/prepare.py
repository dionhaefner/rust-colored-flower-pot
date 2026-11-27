"""Prepare a precomputed demo catalog from checksum-pinned raster samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
PINS = ROOT / "tests/fixtures/navigation.json"
LAYERS = {
    ("lagoon", "red"): "s2-red",
    ("lagoon", "green"): "s2-green",
    ("lagoon", "blue"): "s2-blue",
    ("lagoon", "nir"): "s2-nir",
    ("dolomites", "elevation"): "dem",
    ("cape-town", "elevation"): "dem-south",
}
DEM_CREDIT = (
    "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided "
    "under COPERNICUS by the European Union and ESA; all rights reserved"
)


def prepare(
    binary: Path,
    out: Path,
    bucket: str = "",
    sources: Path | None = None,
    runtime_data: Path | None = None,
) -> dict:
    binary = binary.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "data").mkdir()
    pins = json.loads(PINS.read_text())["sources"]
    rows = []
    for (scene, band), asset in LAYERS.items():
        row = {**pins[asset], "scene": scene, "band": band}
        if band == "elevation":
            row["attribution"] = f"Produced using Copernicus WorldDEM-30 {DEM_CREDIT}"
            row["source_notice"] = DEM_CREDIT
        target = out / "data" / row["file"]
        if sources:
            shutil.copyfile(sources / row["file"], target)
        else:
            with (
                urlopen(row["url"], timeout=120) as download,
                target.open("wb") as file,
            ):
                shutil.copyfileobj(download, file)
        with target.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != row["sha256"]:
                raise ValueError(f"sample checksum mismatch: {target}")
        rows.append(row)
    runtime = binary.parent.parent
    env = {
        **os.environ,
        "TERRACOTTA_RUNTIME_ROOT": str(runtime),
        "TERRACOTTA_GDAL_LIBRARY": str(runtime / "lib/libgdal.so"),
        "LD_LIBRARY_PATH": str(runtime / "lib"),
        "GDAL_DATA": str(runtime / "share/gdal"),
        "PROJ_DATA": str(runtime / "share/proj"),
    }
    database = out / "catalog.sqlite"

    def operation(request):
        request["path"] = str(database)
        instruction = out / "operation.json"
        instruction.write_text(json.dumps(request))
        subprocess.run(
            [str(binary), "data-operation", str(instruction)], check=True, env=env
        )

    operation(
        {
            "operation": "create",
            "keys": [
                {"key": "scene", "description": "Sample acquisition"},
                {"key": "band", "description": "Raster band"},
            ],
        }
    )
    for row in rows:
        operation(
            {
                "operation": "insert",
                "keys": [row["scene"], row["band"]],
                "raster": str(out / "data" / row["file"]),
            }
        )
    with sqlite3.connect(database) as connection:
        for row in rows:
            row["object_key"] = f"rasters/{row['sha256']}/{row['file']}"
            path = (
                f"s3://{bucket}/{row['object_key']}"
                if bucket
                else str((runtime_data or out / "data") / row["file"])
            )
            connection.execute(
                "UPDATE datasets SET path = ? WHERE scene = ? AND band = ?",
                (path, row["scene"], row["band"]),
            )
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata FROM metadata WHERE scene = ? AND band = ?",
                    (row["scene"], row["band"]),
                ).fetchone()[0]
            )
            metadata["attribution"] = row["attribution"]
            connection.execute(
                "UPDATE metadata SET metadata = ? WHERE scene = ? AND band = ?",
                (json.dumps(metadata), row["scene"], row["band"]),
            )
    (out / "operation.json").unlink()
    manifest = {
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "catalog_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "bucket": bucket,
        "fixtures": rows,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bucket", default="")
    parser.add_argument(
        "--sources", type=Path, help="Use previously downloaded rasters"
    )
    parser.add_argument(
        "--runtime-data", type=Path, help="Raster directory in the runtime image"
    )
    args = parser.parse_args()
    prepare(
        args.binary, args.out.resolve(), args.bucket, args.sources, args.runtime_data
    )
