"""Prepare serving derivatives from pinned real scenes and record their cost."""

from __future__ import annotations

import argparse
import copy
import json
import os
import time
from pathlib import Path

import rasterio
from navigation_benchmark import ROOT, digest, native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sources", type=Path, default=ROOT / ".cache/corpus")
    parser.add_argument("--block-size", type=int, choices=(256, 512, 1024), default=256)
    parser.add_argument("--skip-multiband", action="store_true")
    parser.add_argument(
        "--scenes", nargs="+", default=["imagery", "elevation", "south", "landsat"]
    )
    args = parser.parse_args()
    args.binary, args.out = args.binary.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    original = json.loads((ROOT / "tests/fixtures/navigation.json").read_text())
    prepared = copy.deepcopy(original)
    prepared["scenes"] = [s for s in prepared["scenes"] if s["id"] in args.scenes]
    used = {band for scene in prepared["scenes"] for band in scene["bands"].values()}
    rows = []
    env = dict(os.environ, RCFP_COMPAT_PROFILE="native")
    for scene in prepared["scenes"]:
        source = original["sources"][scene["bands"]["red"]]
        scene["navigation_bounds"] = native(
            args.binary,
            args.out,
            env,
            {
                "operation": "compute_metadata",
                "path": str((args.sources / source["file"]).resolve()),
                "options": {"max_shape": [512, 512]},
            },
        )["bounds"]
    for name in used:
        row = prepared["sources"][name]
        source = (args.sources / row["file"]).resolve()
        assert digest(source) == row["sha256"], name
        output = args.out / row["file"]
        started = time.perf_counter()
        native(
            args.binary,
            args.out,
            env,
            {
                "operation": "optimize",
                "path": str(source),
                "output": str(output),
                "options": {
                    "web_mercator_grid": True,
                    "block_size": args.block_size,
                    "resampling_method": "nearest"
                    if name == "worldcover"
                    else "average",
                },
            },
        )
        rows.append(
            {
                "source": name,
                "seconds": time.perf_counter() - started,
                "input_bytes": source.stat().st_size,
                "output_bytes": output.stat().st_size,
            }
        )
        row.update(
            file=str(output),
            sha256=digest(output),
            url=None,
            derived_from=original["sources"][name]["sha256"],
        )
    for name, row in prepared["sources"].items():
        if name not in used:
            row["file"] = str((args.sources / row["file"]).resolve())
    (args.out / "manifest.json").write_text(json.dumps(prepared, indent=2) + "\n")

    if "imagery" in args.scenes and not args.skip_multiband:
        # Retain original geometry and values when testing asset organization.
        red = (args.sources / original["sources"]["s2-red"]["file"]).resolve()
        intermediate = args.out / "rgb-source.tif"
        started = time.perf_counter()
        with rasterio.open(red) as src:
            profile = dict(
                src.profile,
                count=3,
                interleave="pixel",
                tiled=True,
                blockxsize=1024,
                blockysize=1024,
                compress="deflate",
            )
        with rasterio.open(intermediate, "w", **profile) as dst:
            for index, name in enumerate(("s2-red", "s2-green", "s2-blue"), 1):
                with rasterio.open(
                    args.sources / original["sources"][name]["file"]
                ) as src:
                    for _, window in dst.block_windows(1):
                        dst.write(src.read(1, window=window), index, window=window)
        from rasterio.shutil import copy as raster_copy

        rgb = args.out / "rgb.tif"
        raster_copy(
            intermediate,
            rgb,
            driver="COG",
            BLOCKSIZE=1024,
            COMPRESS="DEFLATE",
            OVERVIEW_RESAMPLING="AVERAGE",
        )
        intermediate.unlink()
        rows.append(
            {
                "source": "rgb-bundled",
                "seconds": time.perf_counter() - started,
                "input_bytes": sum(
                    (args.sources / original["sources"][n]["file"]).stat().st_size
                    for n in ("s2-red", "s2-green", "s2-blue")
                ),
                "output_bytes": rgb.stat().st_size,
            }
        )
        bundled = copy.deepcopy(original)
        bundled["scenes"] = [s for s in bundled["scenes"] if s["id"] == "imagery"]
        bundled["sources"] = {
            "rgb": {
                "file": str(rgb),
                "sha256": digest(rgb),
                "url": None,
                "attribution": original["sources"]["s2-red"]["attribution"],
            }
        }
        bundled["scenes"][0].update(
            bands={"red": "rgb", "green": "rgb", "blue": "rgb"},
            band_indexes={"red": 1, "green": 2, "blue": 3},
        )
        (args.out / "multiband-manifest.json").write_text(
            json.dumps(bundled, indent=2) + "\n"
        )
    (args.out / "preparation.json").write_text(
        json.dumps({"binary_sha256": digest(args.binary), "sources": rows}, indent=2)
        + "\n"
    )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
