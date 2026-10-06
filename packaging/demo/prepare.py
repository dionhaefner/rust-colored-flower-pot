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

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs/examples/data"


def prepare(binary: Path, out: Path, bucket: str = "") -> dict:
    binary = binary.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "data").mkdir()
    rows = json.loads((DATA / "manifest.json").read_text())["fixtures"]
    rows = [row for row in rows if row["id"] in ("red", "green", "blue", "nir")]
    for row in rows:
        source = DATA / row["file"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"sample checksum mismatch: {source}")
        shutil.copy2(source, out / "data" / row["file"])
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
                {"key": "band", "description": "Sentinel-2 band"},
            ],
        }
    )
    for row in rows:
        operation(
            {
                "operation": "insert",
                "keys": ["lagoon", row["id"]],
                "raster": str(DATA / row["file"]),
            }
        )
    with sqlite3.connect(database) as connection:
        for row in rows:
            row["object_key"] = f"rasters/{row['sha256']}/{row['file']}"
            path = (
                f"s3://{bucket}/{row['object_key']}"
                if bucket
                else f"/var/task/data/{row['file']}"
            )
            connection.execute(
                "UPDATE datasets SET path = ? WHERE scene = ? AND band = ?",
                (path, "lagoon", row["id"]),
            )
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata FROM metadata WHERE scene = ? AND band = ?",
                    ("lagoon", row["id"]),
                ).fetchone()[0]
            )
            metadata["attribution"] = row["attribution"]
            connection.execute(
                "UPDATE metadata SET metadata = ? WHERE scene = ? AND band = ?",
                (json.dumps(metadata), "lagoon", row["id"]),
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
    args = parser.parse_args()
    prepare(args.binary, args.out.resolve(), args.bucket)
