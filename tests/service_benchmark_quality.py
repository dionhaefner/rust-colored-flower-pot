"""Inspect saved benchmark images against independent GDAL output-grid reads.

This reports differences between sampling contracts; Terracotta's intermediate
grid is not assumed to equal a direct output-grid warp. No render is a golden.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from native_rendering import bounds_for_tile, expected
from navigation_benchmark import ROOT, digest
from PIL import Image
from rasterio.enums import Resampling
from service_benchmark import COLORS, ENGINES


def reference(scene, mode, kind, xyz, sources, destination):
    zoom, x, y = xyz
    bounds = bounds_for_tile(x, y, zoom)
    bands = (
        ["nir", "red"]
        if kind == "expression"
        else (["red", "green", "blue"] if mode == "rgb" else ["red"])
    )
    samples, validity = [], []
    for band in bands:
        source = "s2-nir" if band == "nir" else scene["bands"][band]
        values, valid = expected(
            sources[source],
            bounds,
            (256, 256),
            Resampling.nearest if scene.get("categorical") else Resampling.bilinear,
            destination,
        )
        samples.append(values)
        validity.append(valid)
    image = np.zeros((256, 256, 4), dtype="uint8")
    if scene.get("categorical"):
        for value, color in COLORS.items():
            image[(samples[0] == value) & validity[0]] = color
        return image
    lo, hi = scene["range"]
    if kind == "style":
        lo, hi = lo + (hi - lo) * 0.1, lo + (hi - lo) * 0.8
    if kind == "expression":
        with np.errstate(divide="ignore", invalid="ignore"):
            values = samples[0] / samples[1]
        validity = [validity[0] & validity[1] & np.isfinite(values)]
        samples, lo, hi = [values], 0, 2
    channels = []
    for values, valid in zip(samples, validity):
        channels.append(
            np.where(
                valid,
                np.rint(1 + np.clip((values - lo) * (1 / (hi - lo)), 0, 1) * 254),
                0,
            ).astype("uint8")
        )
    for channel in range(3):
        image[:, :, channel] = channels[channel if len(channels) == 3 else 0]
    image[:, :, 3] = np.any(image[:, :, :3] != 0, axis=2) * 255
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = json.loads((args.results / "report.json").read_text())
    manifest = json.loads((ROOT / "tests/fixtures/navigation.json").read_text())
    scenes = {scene["id"]: scene for scene in manifest["scenes"]}
    sources = {
        key: ROOT / ".cache/corpus" / value["file"]
        for key, value in report["sources"].items()
    }
    for key, path in sources.items():
        assert digest(path) == report["sources"][key]["sha256"]
    references, rows, seen = {}, [], set()
    for case in report["cases"]:
        if case["trial"] != 0 or case["users"] != 1:
            continue
        scene = scenes[case["scene"]]
        for request in case["requests"]:
            if request["status"] != 200 or "image" not in request:
                continue
            style = (
                request["kind"]
                if request["kind"] in ("style", "expression")
                else "ordinary"
            )
            identity = (case["scene"], case["mode"], style, *request["xyz"])
            if (case["engine"], identity) in seen:
                continue
            seen.add((case["engine"], identity))
            if identity not in references:
                references[identity] = reference(
                    scene,
                    case["mode"],
                    style,
                    request["xyz"],
                    sources,
                    args.out / "oracle.vrt",
                )
            oracle = references[identity]
            actual = np.asarray(
                Image.open(args.results / case["folder"] / request["image"]).convert(
                    "RGBA"
                )
            )
            assert actual.shape == oracle.shape
            valid, expected_valid = actual[:, :, 3] != 0, oracle[:, :, 3] != 0
            intersection, union = valid & expected_valid, valid | expected_valid
            differences = np.abs(
                actual[:, :, :3].astype("int16") - oracle[:, :, :3].astype("int16")
            )
            errors = differences[intersection]
            bbox = {
                key.lower(): value for key, value in request["headers"].items()
            }.get("content-bbox")
            if bbox:
                assert np.allclose(
                    list(map(float, bbox.split(","))),
                    bounds_for_tile(
                        request["xyz"][1], request["xyz"][2], request["xyz"][0]
                    ),
                    rtol=0,
                    atol=1e-7,
                )
            if scene.get("categorical"):
                colors = {tuple(c) for c in COLORS.values()} | {(0, 0, 0, 0)}
                assert all(
                    tuple(c) in colors for c in np.unique(actual.reshape(-1, 4), axis=0)
                )
            rows.append(
                {
                    "engine": case["engine"],
                    "identity": identity,
                    "mask_difference_pixels": int(
                        np.count_nonzero(valid != expected_valid)
                    ),
                    "mask_iou": float(intersection.sum() / union.sum())
                    if union.any()
                    else 1.0,
                    "mean_channel_difference": float(errors.mean())
                    if errors.size
                    else 0,
                    "p99_channel_difference": float(np.percentile(errors, 99))
                    if errors.size
                    else 0,
                    "max_channel_difference": int(errors.max()) if errors.size else 0,
                    "valid_intersection_pixels": int(intersection.sum()),
                    "value_difference_pixels": int(
                        np.count_nonzero(
                            np.any(differences != 0, axis=2) & intersection
                        )
                    ),
                    "pixels_above_one_level": int(
                        np.count_nonzero(np.any(differences > 1, axis=2) & intersection)
                    ),
                    "categorical": bool(scene.get("categorical")),
                }
            )
    summary = {}
    for engine in ENGINES:
        selected = [row for row in rows if row["engine"] == engine]
        continuous = [row for row in selected if not row["categorical"]]
        categories = [row for row in selected if row["categorical"]]
        summary[engine] = {
            "checks": len(selected),
            "mask_difference_pixels": sum(
                r["mask_difference_pixels"] for r in selected
            ),
            "worst_mask_iou": min(r["mask_iou"] for r in selected),
            "mean_of_tile_channel_errors": float(
                np.mean([r["mean_channel_difference"] for r in selected])
            ),
            "worst_channel_difference": max(
                r["max_channel_difference"] for r in selected
            ),
            "exact_category_tiles": sum(
                r["categorical"]
                and r["mask_difference_pixels"] == 0
                and r["max_channel_difference"] == 0
                for r in selected
            ),
            "continuous_mean_tile_channel_error": float(
                np.mean([r["mean_channel_difference"] for r in continuous])
            ),
            "continuous_max_channel_error": max(
                r["max_channel_difference"] for r in continuous
            ),
            "category_difference_pixels": sum(
                r["value_difference_pixels"] for r in categories
            ),
            "category_compared_pixels": sum(
                r["valid_intersection_pixels"] for r in categories
            ),
        }
    result = {
        "binary_sha256": report["binary_sha256"],
        "summary": summary,
        "cases": rows,
        "native_direct_grid_passed": all(
            row["mask_difference_pixels"] == 0
            and row["max_channel_difference"] <= (0 if row["categorical"] else 1)
            for row in rows
            if row["engine"] == "rcfp"
        ),
        "interpretation": "Independent direct-grid oracle. Intermediate-grid/overview/rounding policies differ; differences are reported, not relabeled as passes.",
    }
    (args.out / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    if not result["native_direct_grid_passed"]:
        raise SystemExit("Native output-grid fidelity check failed")


if __name__ == "__main__":
    main()
