"""Prepare and verify immutable, reduced-resolution native mosaic snapshots.

Requires NumPy, Rasterio and the native CLI.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.windows import Window


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def attribute_identity(value):
    if isinstance(value, dict):
        value = {k: attribute_identity(v) for k, v in value.items()}
    elif isinstance(value, list):
        value = [attribute_identity(v) for v in value]
    elif type(value) is float and value == 0:
        value = 0.0
    return identity(value)


class Cli:
    def __init__(self, binary):
        self.binary = str(Path(binary).resolve())

    def __call__(self, operation, **fields):
        with tempfile.TemporaryDirectory(prefix="rcfp-mosaic-") as directory:
            request = Path(directory) / "request.json"
            request.write_text(json.dumps(dict(operation=operation, **fields)))
            result = subprocess.run(
                [
                    self.binary,
                    "--compat-profile",
                    "native",
                    "data-operation",
                    str(request),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        return json.loads(result.stdout)


def sources(layer):
    paths = set()
    for observation in layer["observations"]:
        for asset in observation["assets"].values():
            path = Path(asset["location"])
            if not path.is_file() or path.suffix.lower() not in {".tif", ".tiff"}:
                raise ValueError(
                    "snapshot preparation requires local self-contained TIFFs"
                )
            paths.add(path)
            for suffix in (".msk", ".ovr", ".aux.xml"):
                if Path(str(path) + suffix).exists():
                    raise ValueError(
                        "snapshot preparation requires TIFFs without sidecars"
                    )
    return {str(path): digest(path) for path in sorted(paths)}


def validate_spec(layer, spec, aggregation, factor, block, max_pixels):
    if "mosaic" not in layer:
        raise ValueError("source layer must be an indexed mosaic")
    grid = spec["grid"]
    h, w = grid["size"]
    if grid["crs"] != "EPSG:3857" or grid["resampling"] != "nearest":
        raise ValueError("snapshot base grid requires EPSG:3857 and nearest sampling")
    if not 1 <= factor <= 32 or not 1 <= block <= 512 or block * factor > 2048:
        raise ValueError("factor must be 1–32 and base blocks must fit 2048 pixels")
    if h <= 0 or w <= 0 or h * w > max_pixels or h % factor or w % factor:
        raise ValueError(
            "base shape must fit the pixel budget and be divisible by factor"
        )
    if (
        any(not math.isfinite(v) for v in grid["bounds"])
        or grid["bounds"][0] >= grid["bounds"][2]
        or grid["bounds"][1] >= grid["bounds"][3]
    ):
        raise ValueError("snapshot bounds must be finite and increasing")
    categorical = any(c.get("categories") for c in spec["channels"])
    filters = [layer.get("selection", {}), spec.get("selection", {})]
    for observation in layer["observations"]:
        if any(
            name not in observation["attributes"]
            or attribute_identity(observation["attributes"][name])
            != attribute_identity(value)
            for selection in filters
            for name, value in selection.items()
        ):
            continue
        for binding in spec["bindings"].values():
            asset = observation["assets"][binding["asset"]]
            selector = binding["band"]
            if isinstance(selector, str):
                selector = asset["band_names"].get(selector) or next(
                    b["index"]
                    for b in asset["metadata"]["bands"]
                    if b["name"] == selector
                )
            band = asset["metadata"]["bands"][selector - 1]
            categorical |= (
                selector in asset["categorical_bands"]
                or bool(band["categories"])
                or band["color_interpretation"] == "Palette"
            )
    if aggregation == "average" and categorical:
        raise ValueError("categorical snapshots require nearest aggregation")
    return categorical


def reduce_samples(samples, factor, aggregation):
    reduced = []
    for sample in samples:
        shape = sample["shape"]
        values = np.array(sample["values"], dtype="float64").reshape(shape)
        valid = ~np.array(sample["mask"], dtype=bool).reshape(shape)
        if aggregation == "nearest":
            reduced.append(
                np.where(valid, values, np.nan)[
                    factor // 2 :: factor, factor // 2 :: factor
                ]
            )
        else:
            sh = (shape[0] // factor, factor, shape[1] // factor, factor)
            count = valid.reshape(sh).sum(axis=(1, 3))
            total = np.where(valid, values, 0).reshape(sh).sum(axis=(1, 3))
            reduced.append(
                np.divide(
                    total, count, out=np.full(count.shape, np.nan), where=count > 0
                )
            )
    return reduced


def prepare(
    cli,
    database,
    name,
    spec,
    out,
    *,
    factor=4,
    block=128,
    aggregation="average",
    max_pixels=67_108_864,
):
    database = Path(database).resolve()
    layer = cli("layer_get", path=str(database), id=name)
    categorical = validate_spec(layer, spec, aggregation, factor, block, max_pixels)
    parents = sources(layer)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    product = out / "mosaic.tif"
    h, w = spec["grid"]["size"]
    oh, ow = h // factor, w // factor
    transform = from_bounds(*spec["grid"]["bounds"], w, h)
    blocks = []
    try:
        with rasterio.open(
            product,
            "w",
            driver="GTiff",
            height=oh,
            width=ow,
            count=len(spec["channels"]),
            dtype="float64",
            nodata=np.nan,
            crs="EPSG:3857",
            transform=transform * rasterio.Affine.scale(factor),
            tiled=True,
            blockxsize=128,
            blockysize=128,
            compress="deflate",
        ) as dst:
            for y in range(0, oh, block):
                for x in range(0, ow, block):
                    bh, bw = min(block, oh - y), min(block, ow - x)
                    window = Window(x * factor, y * factor, bw * factor, bh * factor)
                    request = copy.deepcopy(spec)
                    request["grid"]["size"] = [bh * factor, bw * factor]
                    request["grid"]["bounds"] = list(
                        rasterio.windows.bounds(window, transform)
                    )
                    result = cli(
                        "layer_samples",
                        path=str(database),
                        id=name,
                        spec=request,
                        provenance=True,
                    )
                    arrays = reduce_samples(result["samples"], factor, aggregation)
                    for i, array in enumerate(arrays, 1):
                        dst.write(array, i, window=Window(x, y, bw, bh))
                    counts = {}
                    for channel, owners in enumerate(result["provenance"]):
                        owners = np.array(
                            [-1 if v is None else v for v in owners]
                        ).reshape(request["grid"]["size"])
                        if aggregation == "nearest":
                            owners = owners[
                                factor // 2 :: factor, factor // 2 :: factor
                            ]
                        for owner, count in zip(*np.unique(owners, return_counts=True)):
                            if owner >= 0:
                                oid = result["contributors"][owner]
                                counts.setdefault(oid, [0] * len(arrays))[channel] = (
                                    int(count)
                                )
                    blocks.append({"window": [x, y, bw, bh], "contributors": counts})
        if (
            cli("layer_get", path=str(database), id=name) != layer
            or sources(layer) != parents
        ):
            raise ValueError("source changed during snapshot preparation")
        manifest = {
            "format": "rcfp-mosaic-product-1",
            "created_at": datetime.now(UTC).isoformat(),
            "source_database": str(database),
            "source_layer": name,
            "source_definition": identity(layer),
            "sources": parents,
            "spec": spec,
            "factor": factor,
            "aggregation": aggregation,
            "order": "align, calibrate, evaluate per observation, ordered overlap, reduce per channel",
            "product_sha256": digest(product),
            "blocks": blocks,
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        channels = copy.deepcopy(spec["channels"])
        for i, channel in enumerate(channels, 1):
            channel["expression"] = f"v{i}"
        default = copy.deepcopy(spec)
        default.pop("selection", None)
        default.update(
            bindings={
                f"v{i}": {"asset": "image", "band": i}
                for i in range(1, len(channels) + 1)
            },
            values="raw",
            channels=channels,
        )
        default["grid"]["size"] = [min(256, oh), min(256, ow)]
        definition = {
            "observations": [
                {
                    "id": "snapshot",
                    "assets": {
                        "image": {
                            "location": str(product),
                            "revision": manifest["product_sha256"],
                            "categorical_bands": list(range(1, len(channels) + 1))
                            if categorical
                            else [],
                        }
                    },
                    "attributes": {"provenance": str(out / "manifest.json")},
                }
            ],
            "overlap": "first_valid",
            "mosaic": layer["mosaic"],
            "default_render": default,
        }
        (out / "layer.json").write_text(json.dumps(definition, indent=2) + "\n")
        return manifest
    except BaseException:
        (out / "layer.json").unlink(missing_ok=True)
        (out / "manifest.json").unlink(missing_ok=True)
        product.unlink(missing_ok=True)
        raise


def check(cli, manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    layer = cli(
        "layer_get", path=manifest["source_database"], id=manifest["source_layer"]
    )
    if (
        identity(layer) != manifest["source_definition"]
        or sources(layer) != manifest["sources"]
    ):
        raise ValueError(
            "mosaic snapshot is stale; regenerate before replacing the served layer"
        )
    if digest(manifest_path.parent / "mosaic.tif") != manifest["product_sha256"]:
        raise ValueError("mosaic snapshot checksum mismatch")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("prepare")
    make.add_argument("database", type=Path)
    make.add_argument("layer")
    make.add_argument("--spec", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--factor", type=int, default=4)
    make.add_argument("--block-size", type=int, default=128)
    make.add_argument(
        "--aggregation", choices=("average", "nearest"), default="average"
    )
    make.add_argument("--max-pixels", type=int, default=67_108_864)
    verify = commands.add_parser("check")
    verify.add_argument("manifest", type=Path)
    args = parser.parse_args()
    cli = Cli(args.binary)
    if args.command == "check":
        check(cli, args.manifest)
    else:
        prepare(
            cli,
            args.database,
            args.layer,
            json.loads(args.spec.read_text()),
            args.out,
            factor=args.factor,
            block=args.block_size,
            aggregation=args.aggregation,
            max_pixels=args.max_pixels,
        )


if __name__ == "__main__":
    main()
