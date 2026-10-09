"""Profile-specific fixes, checked through the native CLI and HTTP boundaries."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import Affine

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rows = []

    def run(profile, name, fn):
        try:
            fn()
            row = {"profile": profile, "case": name, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - report every independent case
            row = {
                "profile": profile,
                "case": name,
                "status": "failed",
                "error": str(error),
            }
        rows.append(row)
        print(f"{row['status']}: {profile} {name}", flush=True)

    for fixture in json.loads((CORPUS / "manifest.json").read_text())["fixtures"]:
        check(
            hashlib.sha256((CORPUS / fixture["file"]).read_bytes()).hexdigest()
            == fixture["sha256"],
            "fixture checksum",
        )

    # Sign/constant edge cases have no suitable retained real fixture. These tiny
    # inputs isolate extrema; rotated derivatives below retain pinned real pixels.
    with rasterio.open(CORPUS / "red.tif") as src:
        template = src.profile.copy()
        real = src.read(1)
    for name, data in {
        "negative": np.arange(-256, 0, dtype=np.int16).reshape(16, 16),
        "constant": np.full((16, 16), -7, dtype=np.int16),
        "mixed": np.arange(-128, 128, dtype=np.int16).reshape(16, 16),
    }.items():
        options = dict(
            template,
            width=16,
            height=16,
            dtype="int16",
            nodata=None,
            tiled=True,
            blockxsize=16,
            blockysize=16,
        )
        with rasterio.open(out / f"{name}.tif", "w", **options) as dst:
            dst.write(data, 1)
    transform = Affine(10, 3, 500000, 2, -10, 5000000)
    # An opaque window lets the expected footprint be calculated independently
    # from its four affine corners, in projected meters before WGS84 conversion.
    check(np.all(real[:32, :32] != 0), "rotated window must be fully valid")
    with rasterio.open(
        out / "rotated.tif",
        "w",
        **dict(
            template,
            width=32,
            height=32,
            transform=transform,
            tiled=True,
            blockxsize=16,
            blockysize=16,
        ),
    ) as dst:
        dst.write(real[:32, :32], 1)
    # Keep a nonrectangular mask on a separate real-pixel derivative.
    masked = real[:32, :32].copy()
    masked[:16, :16] = 0
    with rasterio.open(
        out / "rotated-mask.tif",
        "w",
        **dict(
            template,
            width=32,
            height=32,
            transform=transform,
            tiled=True,
            blockxsize=16,
            blockysize=16,
        ),
    ) as dst:
        dst.write(masked, 1)

    def exercise_profile(profile):
        legacy = profile != "native"
        folder = out / profile
        folder.mkdir()
        database = folder / "catalog.sqlite"
        env = dict(
            os.environ,
            RCFP_COMPAT_PROFILE=profile,
            TC_RASTER_WORKERS="3",
            TC_RASTER_QUEUE_CAPACITY="64",
            TC_RASTER_CACHE_BYTES="0",
        )

        def command(*argv):
            return subprocess.run(
                [str(binary), "--compat-profile", profile, *map(str, argv)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        def op(operation, path=database, **values):
            request = folder / "operation.json"
            request.write_text(
                json.dumps(dict(operation=operation, path=str(path), **values))
            )
            result = command("data-operation", request)
            check(result.returncode == 0, result.stderr)
            value = json.loads(result.stdout)
            check(not isinstance(value, dict) or "error" not in value, str(value))
            return value

        op(
            "create",
            keys=[
                {"key": "scene", "description": ""},
                {"key": "band", "description": ""},
            ],
        )
        for band in ("red", "green", "blue"):
            op("insert", keys=["lagoon", band], raster=str(CORPUS / f"{band}.tif"))
        metadata = op("metadata", keys=["lagoon", "red"])

        def temporary_catalog_cleanup():
            temporary = folder / "temporary-catalog"
            temporary.mkdir()
            result = subprocess.run(
                [
                    str(binary),
                    "--compat-profile",
                    profile,
                    "serve",
                    "-r",
                    str(CORPUS / "{band}.tif"),
                    "--bind",
                    "127.0.0.1:invalid",
                ],
                env=dict(
                    env,
                    TMPDIR=str(temporary),
                    TEMP=str(temporary),
                    TMP=str(temporary),
                ),
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            check(result.returncode != 0, "invalid bind address succeeded")
            check("GDAL " in result.stderr, "temporary catalog did not open")
            check(not list(temporary.iterdir()), "temporary catalog was retained")

        run(profile, "serve-pattern-temporary-cleanup", temporary_catalog_cleanup)

        def replacement():
            op("insert", keys=["replace", "red"], raster=str(CORPUS / "red.tif"))
            previous = op("metadata_stored", keys=["replace", "red"])
            op(
                "insert",
                keys=["replace", "red"],
                raster=str(CORPUS / "green.tif"),
                skip_metadata=True,
            )
            stored = op("metadata_stored", keys=["replace", "red"])
            check(
                stored == previous if legacy else stored is None,
                "replacement retained stale metadata",
            )
            lazy = op("metadata", keys=["replace", "red"])
            expected = previous if legacy else op("metadata", keys=["lagoon", "green"])
            check(
                lazy["range"] == expected["range"] and lazy["mean"] == expected["mean"],
                "lazy replacement metadata",
            )

        run(profile, "replacement-metadata", replacement)

        def ingest():
            db = folder / "ingest.sqlite"
            op("create", path=db, keys=[{"key": "name", "description": ""}])
            op("insert", path=db, keys=["a"], raster=str(CORPUS / "green.tif"))
            before = op("metadata_stored", path=db, keys=["a"])
            inputs = folder / "inputs"
            inputs.mkdir()
            shutil.copyfile(CORPUS / "red.tif", inputs / "a.tif")
            (inputs / "b.tif").write_bytes(b"invalid raster")
            result = command("ingest", inputs / "{name}.tif", "-o", db)
            check(result.returncode != 0, "bad raster ingest succeeded")
            datasets = op("datasets", path=db)
            expected_path = inputs / "a.tif" if legacy else CORPUS / "green.tif"
            check(
                datasets == [{"keys": ["a"], "path": str(expected_path)}],
                "ingest failure did not preserve expected transaction behavior",
            )
            if not legacy:
                check(
                    op("metadata_stored", path=db, keys=["a"]) == before,
                    "rollback lost original metadata",
                )
            mismatch = folder / "mismatch.sqlite"
            op("create", path=mismatch, keys=[{"key": "other", "description": ""}])
            result = command(
                "ingest", inputs / "{name}.tif", "-o", mismatch, "--skip-metadata"
            )
            check((result.returncode == 0) == legacy, "key-name mismatch policy")
            check(
                len(op("datasets", path=mismatch)) == (2 if legacy else 0),
                "mismatch wrote unexpected rows",
            )

        run(profile, "ingest-validation-rollback", ingest)

        def extrema():
            for name, low, high in (
                ("negative", -256, -1),
                ("constant", -7, -7),
                ("mixed", -128, 127),
            ):
                full = op(
                    "compute_metadata",
                    path=out / f"{name}.tif",
                    options={"use_chunks": False},
                )
                chunked = op(
                    "compute_metadata",
                    path=out / f"{name}.tif",
                    options={"use_chunks": True},
                )
                check(full["range"] == [low, high], "full extrema")
                expected_max = (
                    -float.fromhex("0x1p-1022") if legacy and high < 0 else high
                )
                check(
                    chunked["range"] == [low, expected_max],
                    f"{name} chunk extrema: {chunked['range']}",
                )

        run(profile, "chunk-extrema", extrema)

        def footprint():
            from rasterio.warp import transform as project

            for name, pixels in (
                ("rotated", [(0, 0), (32, 0), (32, 32), (0, 32)]),
                ("rotated-mask", [(16, 0), (32, 0), (32, 32), (0, 32), (0, 16)]),
            ):
                corners = [transform * p for p in pixels]
                xs, ys = project(
                    template["crs"],
                    "EPSG:4326",
                    [p[0] for p in corners],
                    [p[1] for p in corners],
                )
                expected = np.array(list(zip(xs, ys, strict=True)))
                for options in (
                    {"use_chunks": False},
                    {"use_chunks": True},
                    {"max_shape": [16, 16]},
                ):
                    result = op(
                        "compute_metadata", path=out / f"{name}.tif", options=options
                    )
                    observed = np.array(result["convex_hull"]["coordinates"][0][:-1])
                    distances = np.linalg.norm(
                        observed[:, None, :] - expected[None, :, :], axis=2
                    )
                    matches = len(observed) == len(expected) and np.all(
                        np.min(distances, axis=0) < 1e-9
                    )
                    # Partially valid chunks already use affine geometry in the
                    # legacy contract, whereas opaque chunks use rectangles.
                    if not legacy:
                        check(matches, f"{name} footprint {options}: {observed}")
                    elif name == "rotated":
                        check(
                            not matches, f"legacy rotated behavior changed: {options}"
                        )

        run(profile, "rotated-footprint", footprint)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (folder / "server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    str(binary),
                    "--compat-profile",
                    profile,
                    "serve",
                    "-d",
                    str(database),
                    "--bind",
                    f"127.0.0.1:{port}",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )

            def request(route, **query):
                url = (
                    f"http://127.0.0.1:{port}"
                    + route
                    + ("?" + urlencode(query) if query else "")
                )
                try:
                    response = urllib.request.urlopen(url, timeout=30)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, response.read()

            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        request("/keys")
                        break
                    except urllib.error.URLError:
                        check(
                            server.poll() is None and time.monotonic() < deadline,
                            "server startup",
                        )
                        time.sleep(0.1)

                def pixels(route, **query):
                    status, body = request(route, **query)
                    check(status == 200, body.decode(errors="replace"))
                    return np.asarray(Image.open(io.BytesIO(body)).convert("RGBA"))

                def render_admission():
                    route = "/singleband/lagoon/red/preview.png"
                    query = {"tile_size": "[512,512]", "stretch_range": "[0,3000]"}
                    status, expected = request(route, **query)
                    check(status == 200, "initial render failed")
                    start = threading.Barrier(8)

                    def client():
                        start.wait(timeout=10)
                        for _ in range(8):
                            status, body = request(route, **query)
                            check(status == 200, f"render admission returned {status}")
                            check(body == expected, "concurrent render changed pixels")

                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                        futures = [pool.submit(client) for _ in range(8)]
                        for future in futures:
                            future.result(timeout=60)

                run(profile, "render-admission", render_admission)

                def empty_tiles():
                    for route, query in (
                        ("/singleband/lagoon/red/20/0/0.png", {}),
                        (
                            "/rgb/lagoon/20/0/0.png",
                            {"r": "red", "g": "green", "b": "blue"},
                        ),
                        (
                            "/compute/lagoon/20/0/0.png",
                            {
                                "expression": "v1",
                                "v1": "red",
                                "stretch_range": "[0,3000]",
                            },
                        ),
                    ):
                        image = pixels(route, tile_size="[96,64]", **query)
                        check(
                            image.shape == ((256, 256, 4) if legacy else (96, 64, 4)),
                            f"empty dimensions {image.shape}",
                        )
                        check(np.all(image[:, :, 3] == 0), "empty alpha")
                        check(
                            request(route, tile_size="[4096,4096]", **query)[0] == 400,
                            "empty response bypassed size budget",
                        )

                run(profile, "empty-tile-dimensions", empty_tiles)

                def percentiles():
                    route = "/singleband/lagoon/red/preview.png"
                    selections = [
                        (
                            "p0",
                            metadata["percentiles"][0]
                            if legacy
                            else metadata["range"][0],
                        ),
                        (
                            "p1",
                            metadata["percentiles"][1]
                            if legacy
                            else metadata["percentiles"][0],
                        ),
                        (
                            "p98",
                            metadata["percentiles"][98]
                            if legacy
                            else metadata["percentiles"][97],
                        ),
                    ]
                    if not legacy:
                        selections.extend(
                            [
                                ("p99", metadata["percentiles"][98]),
                                ("p100", metadata["range"][1]),
                            ]
                        )
                    for label, value in selections:
                        selected = pixels(route, stretch_range=json.dumps([0, label]))
                        numeric = pixels(route, stretch_range=json.dumps([0, value]))
                        check(np.array_equal(selected, numeric), f"percentile {label}")
                    for label in ["p99", "p100", "p101"] if legacy else ["p101"]:
                        check(
                            request(route, stretch_range=json.dumps([0, label]))[0]
                            == 400,
                            f"accepted {label}",
                        )

                run(profile, "percentile-labels", percentiles)

                def http_validation():
                    check(
                        request("/datasets", order_by="scene")[0]
                        == (400 if legacy else 200),
                        "bare order_by",
                    )
                    for columns in ([], ["unknown"], ["keys"]):
                        check(
                            request(
                                "/metadata/lagoon/red", columns=json.dumps(columns)
                            )[0]
                            == (500 if legacy else 400),
                            "metadata columns status",
                        )
                    route = "/singleband/lagoon/red/preview.png"
                    check(
                        request(route, stretch_range="[3000,0]")[0]
                        == (200 if legacy else 400),
                        "singleband reversed range",
                    )
                    check(
                        request(
                            "/rgb/lagoon/preview.png",
                            r="red",
                            g="green",
                            b="blue",
                            r_range="[3000,0]",
                        )[0]
                        == 400,
                        "RGB reversed range",
                    )
                    check(
                        request(
                            "/compute/lagoon/preview.png",
                            v1="red",
                            expression="v1",
                            stretch_range="[3000,0]",
                        )[0]
                        == 400,
                        "compute reversed range",
                    )
                    check(
                        request(
                            "/compute/lagoon/preview.png",
                            v1="red",
                            expression="v1",
                            stretch_range="[1,1]",
                        )[0]
                        == (400 if legacy else 200),
                        "constant compute range",
                    )
                    spec = json.loads(request("/swagger.json")[1])
                    if not legacy:
                        check(
                            spec["info"]["description"] == "RCFP raster tile service.",
                            "native service description",
                        )
                    check(
                        ("/metadata" in spec["paths"]) != legacy,
                        "batch metadata specification",
                    )
                    check(
                        op("spec")["paths"] == spec["paths"],
                        "CLI and HTTP specifications differ",
                    )

                run(profile, "http-validation-spec", http_validation)

                def modulo():
                    route = "/compute/lagoon/preview.png"
                    query = {"v1": "red", "stretch_range": "[0,100]"}
                    if legacy:
                        check(
                            request(route, expression="mod(v1)", **query)[0] == 400,
                            "legacy unary mod",
                        )
                        check(
                            request(route, expression="mod(v1,100)", **query)[0] == 400,
                            "legacy binary mod",
                        )
                    else:
                        check(
                            np.array_equal(
                                pixels(route, expression="mod(v1,100)", **query),
                                pixels(route, expression="v1%100", **query),
                            ),
                            "mod differs from %",
                        )
                        check(
                            request(route, expression="mod(v1)", **query)[0] == 400,
                            "unary mod accepted",
                        )

                run(profile, "modulo", modulo)
            finally:
                server.terminate()
                server.wait(timeout=10)

    for profile in ("native", "terracotta-0.10.1"):
        exercise_profile(profile)

    (out / "report.json").write_text(
        json.dumps(
            {
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "cases": rows,
            },
            indent=2,
        )
        + "\n"
    )
    return int(any(row["status"] != "passed" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
