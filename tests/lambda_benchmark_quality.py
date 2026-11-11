"""Compare captured Lambda tiles with independent GDAL output-grid reads."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import rasterio
from native_rendering import bounds_for_tile, expected
from PIL import Image
from rasterio.enums import Resampling

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / ".cache/corpus")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    os.environ["TERRACOTTA_GDAL_LIBRARY"] = str(
        args.runtime.resolve() / "lib/libgdal.so"
    )
    os.environ["PROJ_DATA"] = str(args.runtime.resolve() / "share/proj")
    os.environ["GDAL_DATA"] = str(args.runtime.resolve() / "share/gdal")
    benchmark = json.loads((args.results / "report.json").read_text())
    sources = {}
    for row in benchmark["deployment"]["manifest"]["sources"]:
        if row["scene"] != "sentinel":
            continue
        path = args.corpus / Path(row["key"]).name
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != row["sha256"]:
                raise ValueError("source raster checksum mismatch")
        sources[row["band"]] = path
    requests = [
        json.loads(line)
        for line in (args.results / "requests.jsonl").read_text().splitlines()
    ]
    paths = sorted({row["path"] for row in requests if row["phase"] == "measured"})
    records = []
    samples = {}
    for path in paths:
        parts = urlsplit(path).path.split("/")
        mode = parts[1]
        xyz = tuple(map(int, [*parts[-3:-1], Path(parts[-1]).stem]))
        zoom, x, y = xyz
        channels = []
        for band in ("red",) if mode == "singleband" else ("red", "green", "blue"):
            key = (band, xyz)
            if key not in samples:
                values, valid = expected(
                    sources[band],
                    bounds_for_tile(x, y, zoom),
                    (256, 256),
                    Resampling.bilinear,
                    args.out / "reference.vrt",
                )
                samples[key] = np.where(
                    valid, np.rint(1 + np.clip(values / 3000, 0, 1) * 254), 0
                ).astype("uint8")
            channels.append(samples[key])
        reference = np.zeros((256, 256, 4), dtype="uint8")
        for channel in range(3):
            reference[:, :, channel] = channels[0 if mode == "singleband" else channel]
        reference[:, :, 3] = np.any(reference[:, :, :3] != 0, axis=2) * 255
        filename = hashlib.sha256(path.encode()).hexdigest() + ".png"
        Image.fromarray(reference).save(args.out / filename)
        for engine in ("native", "terracotta"):
            actual = np.array(
                Image.open(args.results / "images" / engine / filename).convert("RGBA")
            )
            if actual.shape != reference.shape:
                raise ValueError("image dimensions differ")
            both = (actual[:, :, 3] != 0) & (reference[:, :, 3] != 0)
            differences = np.abs(
                actual[:, :, :3].astype("int16") - reference[:, :, :3].astype("int16")
            )
            records.append(
                {
                    "engine": engine,
                    "path": path,
                    "exact": bool(np.array_equal(actual, reference)),
                    "reference_valid_pixels": int(np.count_nonzero(reference[:, :, 3])),
                    "mask_disagreements": int(
                        np.count_nonzero(
                            (actual[:, :, 3] != 0) != (reference[:, :, 3] != 0)
                        )
                    ),
                    "rgb_absolute_error_sum": int(differences[both].sum()),
                    "compared_channels": int(np.count_nonzero(both) * 3),
                    "maximum_rgb_absolute_error": int(differences[both].max())
                    if both.any()
                    else 0,
                }
            )
    summary = {}
    for engine in ("native", "terracotta"):
        selected = [row for row in records if row["engine"] == engine]
        count = sum(row["compared_channels"] for row in selected)
        summary[engine] = {
            "tiles": len(selected),
            "exact_tiles": sum(row["exact"] for row in selected),
            "mask_disagreements": sum(row["mask_disagreements"] for row in selected),
            "mean_rgb_absolute_error": sum(
                row["rgb_absolute_error_sum"] for row in selected
            )
            / count
            if count
            else None,
            "maximum_rgb_absolute_error": max(
                row["maximum_rgb_absolute_error"] for row in selected
            ),
        }
    report = {
        "reference": "independent GDAL public warp API, requested output grid, bilinear, automatic source overviews",
        "rasterio_gdal": rasterio.__gdal_version__,
        "summary": summary,
        "records": records,
        "native_exact": all(
            row["exact"] for row in records if row["engine"] == "native"
        ),
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
