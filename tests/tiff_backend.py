"""Qualify exact TIFF access on pinned real pixels and declared format edges."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import ColorInterp, MaskFlags, Resampling
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
TYPES = {
    "uint8": 1,
    "uint16": 2,
    "int16": 3,
    "uint32": 4,
    "int32": 5,
    "float32": 6,
    "float64": 7,
    "uint64": 12,
    "int64": 13,
    "int8": 14,
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def fixtures(out):
    corpus = ROOT / "tests/fixtures/corpus"
    for row in json.loads((corpus / "manifest.json").read_text())["fixtures"]:
        assert digest(corpus / row["file"]) == row["sha256"]
    with rasterio.open(corpus / "edge.tif") as src:
        values, valid = src.read(1), src.read_masks(1) > 0
    with rasterio.open(corpus / "elevation.tif") as src:
        floating = src.read(1)
    out.mkdir()
    sources, identities = {}, {}

    def create(
        name,
        data,
        *,
        nodata=0,
        compression="deflate",
        interleave="pixel",
        mask=False,
        alpha=False,
        overviews=False,
        endian="LITTLE",
        tiled=True,
        bigtiff=False,
        point=False,
    ):
        path = out / f"{name}.tif"
        options = {
            "driver": "GTiff",
            "height": data.shape[-2],
            "width": data.shape[-1],
            "count": data.shape[0],
            "dtype": str(data.dtype),
            "crs": "EPSG:3857",
            "transform": Affine(128, 0, 1260000, 0, -128, 5680000),
            "nodata": nodata,
            "compress": compression,
            "tiled": tiled,
            "interleave": interleave,
            "ENDIANNESS": endian,
            "BIGTIFF": "YES" if bigtiff else "IF_NEEDED",
        }
        if tiled:
            options.update(blockxsize=128, blockysize=128)
        if data.shape[0] >= 3:
            options["photometric"] = "RGB"
        if alpha:
            options["ALPHA"] = "YES"
        if compression in {"deflate", "lzw", "zstd"}:
            options["predictor"] = 3 if data.dtype.kind == "f" else 2
        with (
            rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True),
            rasterio.open(path, "w", **options) as dst,
        ):
            dst.write(data)
            if point:
                dst.update_tags(AREA_OR_POINT="Point")
            if mask:
                dst.write_mask(np.where(valid, 255, 0).astype("uint8"))
            if alpha:
                dst.colorinterp = (
                    ColorInterp.red,
                    ColorInterp.green,
                    ColorInterp.blue,
                    ColorInterp.alpha,
                )
            if overviews:
                dst.build_overviews([2, 4, 8], Resampling.nearest)
        sources[name] = path
        identities[name] = {
            "path": str(path),
            "sha256": digest(path),
            "recipe": {
                "parents": [
                    digest(corpus / "edge.tif"),
                    digest(corpus / "elevation.tif"),
                ],
                "pixels": "real fixture casts or copies; three nonfinite edge values"
                if name == "nonfinite"
                else "real fixture casts or copies",
                "grid": "binary-exact Mercator affine isolates pixel selection",
                "nodata": nodata
                if nodata is None or np.isfinite(nodata)
                else str(nodata),
                "compression": compression,
                "interleave": interleave,
                "internal_mask": mask,
                "alpha": alpha,
                "overviews": overviews,
                "endian": endian,
                "bigtiff": bigtiff,
                "pixel_is_point": point,
            },
        }
        return path

    for dtype in TYPES:
        data = (floating if dtype.startswith("float") else values).astype(dtype)[None]
        create(dtype, data)
        create(dtype + "-big", data, endian="BIG", compression="none", bigtiff=True)
    for compression in ("none", "lzw", "zstd"):
        create(compression, values[None], compression=compression)
    exceptional = floating.astype("float64")[None]
    exceptional[0, 0, :3] = [float("nan"), float("inf"), -float("inf")]
    create("nonfinite", exceptional, nodata=None)
    create("nan-nodata", exceptional, nodata=float("nan"))
    create("point", values[None], point=True)
    create("overview", values[None], overviews=True)
    create("mask", values[None], mask=True, overviews=True)
    create("odd", values[None, :509, :511], nodata=0)
    rgb = np.stack([values % 255, values // 2 % 255, values // 3 % 255]).astype("uint8")
    create("rgb", rgb, nodata=None, overviews=True)
    create("rgb-planar", rgb, nodata=None, interleave="band")
    create("rgb-planar-none", rgb, nodata=None, interleave="band", compression="none")
    rgba = np.concatenate([rgb, np.where(valid, 255, 0).astype("uint8")[None]])
    create("rgba", rgba, nodata=None, alpha=True, overviews=True)
    create("rgba-mask", rgba, nodata=0, mask=True, alpha=True)
    fractional = rgba.astype("float32")
    fractional[3] = np.where(valid, 0.25, 0)
    create("fractional-alpha", fractional, nodata=None, alpha=True)
    create("stripped", values[None], tiled=False)
    create("jpeg", rgb, nodata=None, compression="jpeg")
    shutil.copy2(sources["uint16"], out / "generation.tif")
    return sources, identities


def expectations(sources, out):
    out.mkdir()
    rows = []
    for name, path in sources.items():
        if name in {"jpeg", "stripped", "rgb-planar"}:
            continue
        with rasterio.open(path) as base:
            levels = [0, *range(1, len(base.overviews(1)) + 1)]
        for level in levels:
            options = {"OVERVIEW_LEVEL": level - 1} if level else {}
            with rasterio.open(path, **options) as src:
                width, height = src.width, src.height
                bands = list(range(1, src.count + 1))
                windows = [
                    [0, 0, min(32, width), min(23, height)],
                    [
                        max(0, width - 21),
                        max(0, height - 20),
                        min(21, width),
                        min(20, height),
                    ],
                ]
                if width > 180 and height > 160:
                    windows.append([121, 121, 45, 36])
                alpha = next(
                    (
                        i
                        for i, c in enumerate(src.colorinterp, 1)
                        if c == ColorInterp.alpha
                    ),
                    None,
                )
                for window_index, window in enumerate(windows):
                    data = src.read(bands, window=Window(*window), out_dtype="float64")
                    validity = src.read_masks(bands, window=Window(*window)) != 0
                    alpha_values = (
                        src.read(alpha, window=Window(*window), out_dtype="float64")
                        if alpha
                        else None
                    )
                    for mode in ("native", "asset"):
                        mask = ~np.isfinite(data)
                        if src.nodata is not None:
                            mask |= data == src.nodata
                        if mode == "asset":
                            for i, band in enumerate(bands):
                                if MaskFlags.alpha not in src.mask_flag_enums[band - 1]:
                                    mask[i] |= ~validity[i]
                        elif alpha is None:
                            mask |= ~validity
                        if alpha_values is not None:
                            for i, band in enumerate(bands):
                                if mode != "asset" or band != alpha:
                                    mask[i] |= ~np.isfinite(alpha_values) | (
                                        alpha_values <= 0
                                    )
                        ident = f"{name}-{level}-{window_index}-{mode}"
                        values_path, mask_path = (
                            out / f"{ident}.f64",
                            out / f"{ident}.mask",
                        )
                        output = data.copy()
                        if mode == "asset":
                            output[mask] = src.nodata if src.nodata is not None else 0
                        output.astype("<f8").tofile(values_path)
                        mask.astype("uint8").tofile(mask_path)
                        rows.append(
                            {
                                "id": ident,
                                "path": str(path),
                                "level": level,
                                "window": window,
                                "bands": bands,
                                "mode": mode,
                                "dtypes": [TYPES[src.dtypes[b - 1]] for b in bands],
                                "public_supported": bool(
                                    mode != "asset"
                                    or (
                                        level == 0
                                        and (
                                            src.nodata is None
                                            or np.isfinite(src.nodata)
                                        )
                                    )
                                ),
                                "values": str(values_path),
                                "mask": str(mask_path),
                            }
                        )
    return rows


@contextlib.contextmanager
def origin(sources, delay=0):
    versions = {name: '"' + digest(path) + '"' for name, path in sources.items()}
    counts = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            _, name = self.path.strip("/").split("/", 1)
            if name not in sources:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Length", str(sources[name].stat().st_size))
            self.send_header("ETag", versions[name])
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            time.sleep(delay)
            kind, name = self.path.strip("/").split("/", 1)
            if name not in sources:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = sources[name]
            size = path.stat().st_size
            lo, hi = self.headers["Range"].removeprefix("bytes=").split("-")
            lo, hi = int(lo), min(int(hi), size - 1)
            with path.open("rb") as stream:
                stream.seek(lo)
                body = stream.read(hi - lo + 1)
            calls = counts.get(self.path, 0)
            counts[self.path] = calls + 1
            self.send_response(200 if kind == "full" else 206)
            self.send_header(
                "Content-Range", f"bytes {lo + (kind == 'wrong')}-{hi}/{size}"
            )
            if kind != "unversioned":
                self.send_header(
                    "ETag",
                    '"changed"' if kind == "changing" and calls else versions[name],
                )
            if kind == "short":
                body = body[:-1]
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--target", type=Path, default=ROOT / "build/async-tiff-adoption/target"
    )
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources, identities = fixtures(out / "fixtures")
    rows = expectations(sources, out / "oracle")
    grids = []
    for nearest in (False, True):
        for level in (0, 1, 2, 3):
            step = 128 * 2**level
            window = [4, 5, 16, 12]
            west, north = 1260000 + 4 * step, 5680000 - 5 * step
            grids.append(
                {
                    "id": f"exact-{nearest}-{level}",
                    "path": str(sources["overview"]),
                    "nearest": nearest,
                    "bounds": [west, north - 12 * step, west + 16 * step, north],
                    "size": [12, 16],
                    "supported": True,
                    "level": level,
                    "window": window,
                }
            )
    for name, bounds in (
        ("partial", [1259872, 5678464, 1262048, 5680000]),
        ("resampled", [1260000, 5678464, 1264096, 5680000]),
        ("fractional", [1260064, 5678528, 1262112, 5680064]),
    ):
        grids.append(
            {
                "id": name,
                "path": str(sources["overview"]),
                "nearest": False,
                "bounds": bounds,
                "size": [12, 16],
                "supported": False,
            }
        )
    matrix = {
        "windows": rows,
        "grids": grids,
        "fallbacks": [
            {"id": name, "path": str(sources[name])}
            for name in ("jpeg", "stripped", "rgb-planar")
        ],
        "generation_source": str(out / "fixtures/generation.tif"),
    }
    write(out / "fixtures.json", identities)
    if args.prepare_only:
        write(out / "matrix.json", matrix)
        print(json.dumps({"windows": len(rows), "grids": len(grids)}))
        return
    with origin(sources) as url:
        matrix["grids"] += [
            dict(case, id="remote-" + case["id"], path=f"{url}/good/overview")
            for case in grids
            if case["supported"]
        ]
        matrix["windows"] += [
            dict(
                row,
                id="remote-" + row["id"],
                path=f"{url}/good/{Path(row['path']).stem}",
            )
            for row in rows
            if row["level"] == 0 and row["window"][:2] == [0, 0]
        ]
        matrix["fallbacks"] += [
            {"id": kind, "path": f"{url}/{kind}/uint16"}
            for kind in ("full", "wrong", "short", "changing", "unversioned")
        ]
        write(out / "matrix.json", matrix)
        env = dict(
            os.environ,
            CARGO_TARGET_DIR=str(args.target.resolve()),
            RCFP_TIFF_TEST_MATRIX=str(out / "matrix.json"),
        )
        command = [
            str(ROOT / "packaging/cargo.sh"),
            "test",
            "--locked",
            "--offline",
            "--release",
            "--lib",
            "raster::tiff::tests",
            "--",
            "--include-ignored",
            "--test-threads=1",
            "--nocapture",
        ]
        with (out / "tests.log").open("w") as log:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
    write(
        out / "report.json",
        {
            "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "windows": len(matrix["windows"]),
            "grids": len(matrix["grids"]),
            "fallbacks": len(matrix["fallbacks"]),
            "fixture_manifest_sha256": digest(out / "fixtures.json"),
            "oracle": "Rasterio raw values/masks/dtypes; distinct native and asset validity contracts",
            "exclusions": [
                "authenticated cloud",
                "other platforms",
                "resampled or projected candidate grids",
            ],
        },
    )
    print((out / "report.json").read_text())
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
