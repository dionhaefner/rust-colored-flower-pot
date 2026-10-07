"""Verify the public real-raster corpus independently of candidate output."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = [
    ROOT / "tests/fixtures/corpus/manifest.json",
    ROOT / "tests/fixtures/tile-grids/manifest.json",
]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_fixture(path, row):
    def require(condition, field):
        if not condition:
            raise AssertionError(f"{row['id']}: {field}")

    require(digest(path) == row["sha256"], "file checksum")
    require(bool(row["attribution"]), "attribution")
    require(len(row["parent_sha256"]) == 64, "parent checksum")
    require(
        row.get("source_url", row.get("parent_url", "")).startswith("https://"),
        "source URL",
    )
    with rasterio.open(path) as source:
        pixels, mask = source.read(1), source.read_masks(1)
        require(source.count == 1, "band count")
        require(list(pixels.shape) == row["window"][2:][::-1], "native window size")
        require(source.dtypes[0] == row["dtype"], "dtype")
        require(str(source.crs) == row["crs"], "CRS")
        require(list(source.transform)[:6] == row["transform"], "affine placement")
        require(source.nodata == row["nodata"], "nodata")
        # Manifests hash C-order samples in little-endian storage across hosts.
        packed = pixels.astype(pixels.dtype.newbyteorder("<"), copy=False).tobytes()
        require(
            hashlib.sha256(packed).hexdigest() == row["pixel_sha256"], "decoded pixels"
        )
        require(
            hashlib.sha256(mask.tobytes()).hexdigest() == row["mask_sha256"],
            "decoded mask",
        )
        require(int(np.count_nonzero(mask)) == row["valid_pixels"], "valid count")
        require(
            float(pixels[source.height // 2, source.width // 2]) == row["center_pixel"],
            "center value",
        )
        actual = transform_bounds(source.crs, "EPSG:4326", *source.bounds)
        require(
            np.allclose(actual, row["geographic_bounds"], rtol=0, atol=1e-10),
            "geographic bounds",
        )
    return {"id": row["id"], "sha256": row["sha256"], "status": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    cases = []
    for manifest in MANIFESTS:
        for row in json.loads(manifest.read_text())["fixtures"]:
            try:
                result = check_fixture(manifest.parent / row["file"], row)
            except Exception as error:  # noqa: BLE001 - preserve every fixture outcome
                result = {"id": row["id"], "status": "failed", "error": repr(error)}
            cases.append(result)
    report = {
        "checker_sha256": digest(Path(__file__)),
        "manifests": {str(p.relative_to(ROOT)): digest(p) for p in MANIFESTS},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "rasterio": rasterio.__version__,
            "gdal": rasterio.__gdal_version__,
        },
        "cases": cases,
        "passed": all(row["status"] == "passed" for row in cases),
        "exclusions": [
            "Candidate rendering",
            "Remote acquisition",
            "Performance",
            "Independent adopter usage",
        ],
        "skips": [],
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
