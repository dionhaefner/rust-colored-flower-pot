"""Assess sampled session tiles against independent GDAL output-grid reads."""

import argparse
import functools
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
from native_rendering import bounds_for_tile, expected
from PIL import Image
from rasterio.enums import Resampling

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    runtime = args.runtime.resolve()
    os.environ.update(
        TERRACOTTA_GDAL_LIBRARY=str(runtime / "lib/libgdal.so"),
        PROJ_DATA=str(runtime / "share/proj"),
        GDAL_DATA=str(runtime / "share/gdal"),
    )
    manifest = json.loads((args.results[0] / "report.json").read_text())["deployment"][
        "manifest"
    ]
    sources = {}
    for row in manifest["sources"]:
        if row["scene"] == "lagoon":
            continue
        source = ROOT / ".cache/corpus" / Path(row["key"]).name
        with source.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"]
        sources[(row["scene"], row["band"])] = source

    @functools.lru_cache(maxsize=96)
    def read(scene, band, xyz):
        z, x, y = xyz
        return expected(
            sources[(scene, band)],
            bounds_for_tile(x, y, z),
            (256, 256),
            Resampling.nearest if scene == "worldcover" else Resampling.bilinear,
            args.out / "reference.vrt",
        )

    sampled = {}
    identities = {}
    for result in args.results:
        report = json.loads((result / "report.json").read_text())
        for case in report["cases"]:
            folder = result / case["case"]
            if not (folder / "result.json").exists():
                continue
            data = json.loads((folder / "result.json").read_text())
            for sample in data["images"]:
                key = case["arm"], sample["path"]
                identities.setdefault(key, set()).add(sample["rgba_sha256"])
                sampled[key] = folder / "images" / sample["filename"]
    records = []
    references = {}
    for (arm, path), image_file in sorted(
        sampled.items(), key=lambda item: (item[0][1], item[0][0])
    ):
        parts = urlsplit(path).path.split("/")
        mode, scene = parts[1:3]
        xyz = tuple(map(int, [*parts[-3:-1], Path(parts[-1]).stem]))
        query = parse_qs(urlsplit(path).query)
        key = hashlib.sha256(path.encode()).hexdigest()
        if key not in references:
            reference = np.zeros((256, 256, 4), dtype="uint8")
            if scene == "worldcover":
                values, valid = read(scene, "red", xyz)
                colors = json.loads(query["explicit_color_map"][0])
                for category, rgba in colors.items():
                    reference[valid & (values == int(category))] = rgba
            else:
                channels = ("r", "g", "b") if mode == "rgb" else (None,)
                arrays = []
                for channel in channels:
                    band = query[channel][0] if channel else parts[3]
                    values, valid = read(scene, band, xyz)
                    lo, hi = json.loads(
                        query[channel + "_range" if channel else "stretch_range"][0]
                    )
                    arrays.append(
                        np.where(
                            valid,
                            np.rint(1 + np.clip((values - lo) / (hi - lo), 0, 1) * 254),
                            0,
                        ).astype("uint8")
                    )
                for channel in range(3):
                    reference[:, :, channel] = arrays[channel if mode == "rgb" else 0]
                reference[:, :, 3] = np.any(reference[:, :, :3] != 0, axis=2) * 255
            reference_file = args.out / (key + ".png")
            Image.fromarray(reference).save(reference_file)
            references[key] = reference_file
        reference = np.array(Image.open(references[key]).convert("RGBA"))
        actual = np.array(Image.open(image_file).convert("RGBA"))
        if actual.shape != reference.shape:
            raise ValueError("captured tile dimensions differ from the reference")
        common = (actual[:, :, 3] != 0) & (reference[:, :, 3] != 0)
        delta = np.abs(
            actual[:, :, :3].astype("int16") - reference[:, :, :3].astype("int16")
        )
        records.append(
            {
                "arm": arm,
                "scene": scene,
                "path": path,
                "exact": bool(np.array_equal(actual, reference)),
                "stable_across_samples": len(identities[(arm, path)]) == 1,
                "mask_disagreements": int(
                    np.count_nonzero(
                        (actual[:, :, 3] != 0) != (reference[:, :, 3] != 0)
                    )
                ),
                "reference_valid_pixels": int(np.count_nonzero(reference[:, :, 3])),
                "actual_valid_pixels": int(np.count_nonzero(actual[:, :, 3])),
                "absolute_error_sum": int(delta[common].sum()),
                "compared_channels": int(np.count_nonzero(common) * 3),
                "categorical_compared_pixels": int(np.count_nonzero(common))
                if scene == "worldcover"
                else 0,
                "categorical_label_disagreements": int(
                    np.count_nonzero(np.any(delta != 0, axis=2) & common)
                )
                if scene == "worldcover"
                else 0,
                "maximum_channel_difference": int(delta[common].max())
                if common.any()
                else 0,
            }
        )
    summary = {}
    if not any(r["arm"] == "native" for r in records):
        raise ValueError("no captured native tiles to assess")
    for arm in sorted({r["arm"] for r in records}):
        rows = [r for r in records if r["arm"] == arm]
        count = sum(r["compared_channels"] for r in rows)
        summary[arm] = {
            "tiles": len(rows),
            "exact": sum(r["exact"] for r in rows),
            "stable_across_samples": all(r["stable_across_samples"] for r in rows),
            "mask_disagreements": sum(r["mask_disagreements"] for r in rows),
            "transparent_reference_tiles": sum(
                r["reference_valid_pixels"] == 0 for r in rows
            ),
            "categorical_compared_pixels": sum(
                r["categorical_compared_pixels"] for r in rows
            ),
            "categorical_label_disagreements": sum(
                r["categorical_label_disagreements"] for r in rows
            ),
            "mean_channel_difference": sum(r["absolute_error_sum"] for r in rows)
            / count
            if count
            else 0,
            "maximum_channel_difference": max(
                r["maximum_channel_difference"] for r in rows
            ),
        }
    report = {
        "reference": "Independent GDAL output-grid sampling with requested stretches or exact category palettes",
        "native_exact": all(r["exact"] for r in records if r["arm"] == "native"),
        "summary": summary,
        "records": records,
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
