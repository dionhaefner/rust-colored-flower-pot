"""Exercise an installed RCFP wheel after removing raster test dependencies."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()

    for name in ("rasterio", "numpy", "PIL"):
        if importlib.util.find_spec(name) is not None:
            raise RuntimeError(f"test dependency still installed: {name}")
    requirements = importlib.metadata.requires("rcfp") or []
    if any("rasterio" in requirement.lower() for requirement in requirements):
        raise RuntimeError(f"RCFP still declares Rasterio: {requirements}")

    import rcfp

    if not Path(rcfp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(
            f"RCFP was not imported from the installed wheel: {rcfp.__file__}"
        )
    status, kind, body, _ = rcfp.request(args.database, "GET", "/keys")
    if status != 200 or kind != "application/json":
        raise RuntimeError(f"discovery failed: {status}, {kind}")
    if [row["key"] for row in json.loads(body)["keys"]] != ["scene", "band"]:
        raise RuntimeError("catalog keys changed")
    status, kind, image, _ = rcfp.request(
        args.database,
        "GET",
        "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D",
    )
    if (
        status != 200
        or kind != "image/png"
        or not image.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise RuntimeError(f"raster render failed: {status}, {kind}")
    if struct.unpack(">II", image[16:24]) != (256, 256):
        raise RuntimeError("raster dimensions changed")
    subprocess.run(
        [sys.executable, "-m", "rcfp.cli", "--help"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    print(
        json.dumps(
            {
                "passed": True,
                "requirements": requirements,
                "rasterio_installed": False,
                "png_bytes": len(image),
            }
        )
    )


if __name__ == "__main__":
    main()
