"""Installed-package integration checks against pinned, real raster crops."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "corpus"


def equal(actual, expected, label="value"):
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def decoded(body):
    with Image.open(io.BytesIO(body)) as image:
        check(image.format == "PNG", "response is not a PNG")
        return np.asarray(image.convert("RGBA")).copy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    wheel = args.wheel.resolve()
    check(wheel.is_file() and wheel.suffix == ".whl", "tested wheel is missing")
    out.mkdir(parents=True, exist_ok=False)

    import rcfp
    from rcfp.wsgi import Application

    check(
        Path(rcfp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
        "import is not from installed environment",
    )
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    files = {row["id"]: CORPUS / row["file"] for row in manifest["fixtures"]}
    fixtures = {row["id"]: row for row in manifest["fixtures"]}
    results = []

    def run(case_id, function):
        started = time.monotonic()
        try:
            function()
            record = {"id": case_id, "status": "passed"}
        except Exception as error:  # noqa: BLE001 - report every case failure
            record = {
                "id": case_id,
                "status": "failed",
                "error": str(error),
                "traceback": traceback.format_exc(),
            }
        record["seconds"] = round(time.monotonic() - started, 3)
        results.append(record)
        print(f"{record['status']}: {case_id}", flush=True)

    def fixture_check(row):
        path = files[row["id"]]
        check(path.is_file(), f"missing fixture: {path}")
        equal(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            row["sha256"],
            "fixture bytes",
        )
        with Image.open(path) as image:
            values = np.asarray(image)
        equal(values.shape, (512, 512), "dimensions")
        equal(str(values.dtype), row["dtype"], "dtype")
        equal(
            hashlib.sha256(values.tobytes()).hexdigest(),
            row["pixel_sha256"],
            "source pixels",
        )
        mask = (
            np.full(values.shape, 255, dtype=np.uint8)
            if row["nodata"] is None
            else np.where(values == row["nodata"], 0, 255).astype(np.uint8)
        )
        equal(
            hashlib.sha256(mask.tobytes()).hexdigest(),
            row["mask_sha256"],
            "source mask",
        )
        equal(float(values[256, 256]), row["center_pixel"], "center pixel")
        equal(int(np.count_nonzero(mask)), row["valid_pixels"], "valid count")

    for row in manifest["fixtures"]:
        run(f"fixture.{row['id']}", lambda row=row: fixture_check(row))

    database = out / "catalog.sqlite"
    store = rcfp.DataStore(database)
    run(
        "storage.create",
        lambda: store.create(
            ["scene", "band"],
            key_descriptions={"scene": "Location", "band": "Spectral band"},
        ),
    )
    datasets = [
        ("lagoon", "red", "red"),
        ("lagoon", "green", "green"),
        ("lagoon", "blue", "blue"),
        ("lagoon", "nir", "nir"),
        ("lagoon", "classes", "classes"),
        ("dolomites", "elevation", "elevation"),
        ("edge", "red", "edge"),
    ]
    for scene, band, name in datasets:
        run(
            f"storage.insert.{name}",
            lambda scene=scene, band=band, name=name: store.insert(
                [scene, band], files[name]
            ),
        )

    def response(path, method="GET", headers=None, body=b""):
        status, kind, content, _, extra = rcfp.request_full(
            database, method, path, headers or {}, body
        )
        return status, kind, content, extra

    def json_response(path, method="GET", headers=None, body=b""):
        status, kind, content, extra = response(path, method, headers, body)
        equal(kind, "application/json", "content type")
        return status, json.loads(content), extra

    def keys():
        status, body, _ = json_response("/keys")
        equal(status, 200)
        equal(
            body["keys"],
            [
                {"key": "scene", "description": "Location"},
                {"key": "band", "description": "Spectral band"},
            ],
        )

    run("http.keys.order-description", keys)

    def dataset_filter():
        status, body, _ = json_response("/datasets?scene=lagoon")
        equal(status, 200)
        equal(
            [entry["band"] for entry in body["datasets"]],
            ["blue", "classes", "green", "nir", "red"],
        )
        equal(len(body["datasets"]), 5)

    run("http.datasets.filter-order", dataset_filter)

    def dataset_paging():
        all_rows = json_response("/datasets")[1]["datasets"]
        pages = [
            json_response(f"/datasets?limit=2&page={page}")[1]["datasets"]
            for page in range(4)
        ]
        equal([row for page in pages for row in page], all_rows)
        equal([len(page) for page in pages], [2, 2, 2, 1])

    run("http.datasets.paging-partition", dataset_paging)

    def dataset_multivalue():
        status, body, _ = json_response("/datasets?band=%5Bred%2Cblue%5D")
        equal(status, 200)
        equal({entry["band"] for entry in body["datasets"]}, {"red", "blue"})
        equal(len(body["datasets"]), 3)

    run("http.datasets.multivalue", dataset_multivalue)

    def metadata():
        status, body, _ = json_response("/metadata/lagoon/red")
        equal(status, 200)
        equal(body["keys"], {"scene": "lagoon", "band": "red"})
        check(body["range"][1] > body["range"][0], "empty range")
        equal(body["bounds"], fixtures["red"]["geographic_bounds"], "metadata bounds")

    run("http.metadata.geometry-range", metadata)

    def metadata_batch():
        request = json.dumps(
            {"keys": [["lagoon", "red"], ["lagoon", "green"]]}
        ).encode()
        status, body, _ = json_response(
            "/metadata", "POST", {"content-type": "application/json"}, request
        )
        equal(status, 200)
        check(
            len(body) == 2 or len(body.get("metadata", [])) == 2,
            "batch did not return two records",
        )

    run("http.metadata.batch", metadata_batch)

    def metadata_columns():
        status, body, _ = json_response(
            "/metadata/lagoon/red?columns=%5B%22range%22%5D"
        )
        equal(status, 200)
        check("range" in body, "selected range absent")

    run("http.metadata.columns", metadata_columns)

    def palette():
        status, body, _ = json_response(
            "/colormap?colormap=viridis&stretch_range=%5B0%2C1%5D&num_values=3"
        )
        equal(status, 200)
        check(len(body) >= 1, "colormap response empty")

    run("http.colormap.legend", palette)

    def png(path, size=(256, 256)):
        status, kind, body, _ = response(path)
        equal(status, 200, path)
        equal(kind, "image/png", path)
        array = decoded(body)
        equal((array.shape[1], array.shape[0]), size, path)
        return array

    sb = "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D"
    rgb = "/rgb/lagoon/preview.png?r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D"
    compute = "/compute/lagoon/preview.png?expression=%28v1-v2%29%2F%28v1%2Bv2%29&v1=nir&v2=red&stretch_range=%5B-1%2C1%5D"
    images = {}

    def singleband():
        image = png(sb)
        check(np.count_nonzero(image[:, :, 3]) > 0, "singleband entirely transparent")
        check(len(np.unique(image[:, :, 0])) > 40, "singleband lost contrast")
        images["singleband"] = image

    run("raster.singleband.preview", singleband)

    def rgb_image():
        image = png(rgb)
        check(np.count_nonzero(image[:, :, 3]) > 0, "RGB entirely transparent")
        check(np.any(image[:, :, 0] != image[:, :, 1]), "red/green channels collapsed")
        check(np.any(image[:, :, 1] != image[:, :, 2]), "green/blue channels collapsed")
        images["rgb"] = image

    run("raster.rgb.distinct-channels", rgb_image)

    def rgb_permutation():
        original = images["rgb"]
        swapped = png(rgb.replace("r=red&g=green&b=blue", "r=blue&g=green&b=red"))
        equal(
            int(np.count_nonzero(original[:, :, 3] != swapped[:, :, 3])), 0, "RGB alpha"
        )
        check(
            np.array_equal(original[:, :, 0], swapped[:, :, 2]),
            "red/blue swap changed values",
        )
        check(
            np.array_equal(original[:, :, 2], swapped[:, :, 0]),
            "blue/red swap changed values",
        )

    run("raster.rgb.channel-permutation", rgb_permutation)

    def compute_ndvi():
        image = png(compute)
        check(np.count_nonzero(image[:, :, 3]) > 0, "compute entirely transparent")
        check(len(np.unique(image[:, :, 0])) > 15, "NDVI lost variation")

    run("raster.compute.ndvi", compute_ndvi)

    def compute_identity():
        one = png(
            "/compute/lagoon/preview.png?expression=v1&v1=red&stretch_range=%5B0%2C3000%5D"
        )
        plus_zero = png(
            "/compute/lagoon/preview.png?expression=v1%2B0&v1=red&stretch_range=%5B0%2C3000%5D"
        )
        check(np.array_equal(one, plus_zero), "v1 and v1+0 differ")

    run("raster.compute.identity", compute_identity)

    def classes():
        colors = {
            10: [1, 2, 3, 255],
            20: [4, 5, 6, 255],
            30: [7, 8, 9, 255],
            40: [10, 11, 12, 255],
            50: [13, 14, 15, 255],
            60: [16, 17, 18, 255],
            80: [19, 20, 21, 255],
            90: [22, 23, 24, 255],
        }
        from urllib.parse import quote

        image = png(
            "/singleband/lagoon/classes/preview.png?colormap=explicit&explicit_color_map="
            + quote(json.dumps(colors), safe="")
        )
        observed = set(map(tuple, image.reshape(-1, 4)))
        allowed = {tuple(color) for color in colors.values()} | {(0, 0, 0, 0)}
        check(observed <= allowed, f"unmapped class colors: {observed - allowed}")
        check(len(observed & allowed) >= 3, "categorical rendering collapsed")

    run("raster.categories.exact-palette", classes)

    def edge_mask():
        image = png("/singleband/edge/red/preview.png?stretch_range=%5B0%2C3000%5D")
        alpha = image[:, :, 3]
        check(np.any(alpha == 0) and np.any(alpha == 255), "footprint edge lost")
        check(set(np.unique(alpha)).issubset({0, 255}), "mask alpha is not binary")

    run("raster.edge.alpha", edge_mask)

    def elevation():
        image = png("/singleband/dolomites/elevation/preview.png")
        check(np.count_nonzero(image[:, :, 3]) > 0, "DEM transparent")
        check(len(np.unique(image[:, :, 0])) > 20, "DEM rendering collapsed")

    run("raster.dem.geographic", elevation)

    def custom_size():
        image = png(
            "/singleband/lagoon/red/preview.png?tile_size=%5B96%2C64%5D&stretch_range=%5B0%2C3000%5D",
            (64, 96),
        )
        check(np.any(image[:, :, 3] != 0), "custom size image empty")

    run("raster.preview.nonsquare", custom_size)

    def outside():
        image = png(
            "/singleband/lagoon/red/0/0/0.png?tile_size=%5B64%2C64%5D", (256, 256)
        )
        check(np.any(image[:, :, 3] == 0), "outside coverage not transparent")

    run("raster.tile.outside", outside)

    invalid = [
        ("datasets.invalid-page", "/datasets?page=invalid"),
        ("datasets.invalid-order", "/datasets?order_by=%5Bmissing%5D"),
        (
            "metadata.invalid-columns",
            "/metadata/lagoon/red?columns=%5B%22missing%22%5D",
            500,
        ),
        ("singleband.invalid-size", "/singleband/lagoon/red/preview.png?tile_size=bad"),
        (
            "singleband.invalid-palette",
            "/singleband/lagoon/red/preview.png?colormap=missing",
        ),
        ("rgb.missing-channel", "/rgb/lagoon/preview.png?r=red&g=green"),
        (
            "compute.invalid-syntax",
            "/compute/lagoon/preview.png?expression=v1%2B&v1=red",
        ),
        (
            "compute.invalid-operation",
            "/compute/lagoon/preview.png?expression=v1.size&v1=red",
        ),
    ]
    for item in invalid:
        name, path = item[:2]
        expected_status = item[2] if len(item) > 2 else 400

        def invalid_case(path=path, expected_status=expected_status):
            status, kind, body, _ = response(path)
            equal(status, expected_status, path)
            if expected_status == 500:
                equal(kind, "text/html; charset=utf-8", path)
                check(b"Internal Server Error" in body, "internal error page missing")
            else:
                equal(kind, "application/json", path)
                check(
                    isinstance(json.loads(body).get("message"), str)
                    and bool(json.loads(body)["message"]),
                    "missing error meaning",
                )

        run("http.error." + name, invalid_case)

    def missing_dataset():
        status, body, _ = json_response("/metadata/lagoon/missing")
        equal(status, 404)
        check("message" in body, "missing dataset error absent")

    run("http.error.missing-dataset", missing_dataset)

    def cors():
        status, _, _, headers = response(
            "/keys", "OPTIONS", {"origin": "https://example.com"}
        )
        equal(status, 200)
        equal(headers.get("Access-Control-Allow-Origin"), "https://example.com")

    run("http.cors.preflight", cors)

    def wsgi():
        captured = []
        app = Application(str(database))
        body = b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/keys",
                    "wsgi.input": io.BytesIO(),
                },
                lambda status, headers: captured.append((status, headers)),
            )
        )
        equal(captured[0][0], "200 OK")
        equal([row["key"] for row in json.loads(body)["keys"]], ["scene", "band"])

    run("adapter.wsgi.keys", wsgi)

    def cli_server():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "rcfp.cli",
                "serve",
                "-d",
                str(database),
                "--bind",
                f"127.0.0.1:{port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/keys", timeout=1
                    ) as incoming:
                        equal(incoming.status, 200)
                        equal(
                            [row["key"] for row in json.load(incoming)["keys"]],
                            ["scene", "band"],
                        )
                    break
                except (urllib.error.URLError, TimeoutError):
                    if process.poll() is not None:
                        raise RuntimeError(
                            process.stderr.read().decode(errors="replace")
                        )
                    time.sleep(0.1)
            else:
                raise AssertionError("CLI server did not start")
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}" + sb, timeout=15
            ) as incoming:
                equal(incoming.headers.get_content_type(), "image/png")
                check(
                    np.array_equal(decoded(incoming.read()), images["singleband"]),
                    "CLI HTTP image differs from Python adapter",
                )
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    run("adapter.cli.http-render", cli_server)

    def storage_roundtrip():
        metadata = store.get_metadata(["lagoon", "red"])
        check(isinstance(metadata["bounds"], tuple), "Python bounds type changed")
        equal(tuple(metadata["bounds"]), tuple(fixtures["red"]["geographic_bounds"]))
        store.delete(["edge", "red"])
        try:
            store.get_metadata(["edge", "red"])
        except rcfp.RcfpError:
            pass
        else:
            raise AssertionError("deleted row still readable")
        store.insert(["edge", "red"], files["edge"])

    run("storage.delete-reinsert", storage_roundtrip)

    def batch_rollback():
        before = json_response("/datasets")[1]["datasets"]
        try:
            store.batch(
                [
                    {"operation": "delete", "keys": ["lagoon", "red"]},
                    {
                        "operation": "insert",
                        "keys": ["bad"],
                        "raster": str(files["red"]),
                    },
                ]
            )
        except rcfp.RcfpError:
            pass
        else:
            raise AssertionError("invalid batch did not fail")
        equal(json_response("/datasets")[1]["datasets"], before, "batch rollback")

    run("storage.batch.rollback", batch_rollback)

    def metadata_compute():
        result = rcfp.compute_metadata(files["red"])
        equal(tuple(result["bounds"]), tuple(fixtures["red"]["geographic_bounds"]))
        check(result["range"][1] > result["range"][0], "computed range invalid")

    run("python.compute-metadata", metadata_compute)

    def metadata_modes():
        with Image.open(files["elevation"]) as source:
            values = np.asarray(source)
        full = rcfp.compute_metadata(files["elevation"], use_chunks=False)
        chunked = rcfp.compute_metadata(files["elevation"], use_chunks=True)
        sampled = rcfp.compute_metadata(files["elevation"], max_shape=(64, 64))
        equal(tuple(full["range"]), (float(values.min()), float(values.max())))
        equal(tuple(chunked["range"]), tuple(full["range"]))
        check(
            abs(chunked["mean"] - float(values.mean())) < 0.01, "chunked mean shifted"
        )
        check(
            full["range"][0]
            <= sampled["range"][0]
            <= sampled["range"][1]
            <= full["range"][1],
            "sampled range escaped full range",
        )
        check(
            abs(sampled["mean"] - full["mean"]) < 5, "sampled mean changed materially"
        )

    run("python.metadata.full-chunk-sample", metadata_modes)

    def cli_ingest():
        source_dir = out / "ingest-input"
        source_dir.mkdir()
        shutil.copyfile(files["red"], source_dir / "lagoon_red.tif")
        shutil.copyfile(files["green"], source_dir / "lagoon_green.tif")
        ingested = out / "ingested.sqlite"
        pattern = str(source_dir / "{scene}_{band}.tif")
        command = [
            sys.executable,
            "-m",
            "rcfp.cli",
            "ingest",
            pattern,
            "-o",
            str(ingested),
        ]
        subprocess.run(command, check=True, capture_output=True)
        status, kind, body, _, _ = rcfp.request_full(ingested, "GET", "/datasets")
        equal(status, 200)
        equal(kind, "application/json")
        equal({row["band"] for row in json.loads(body)["datasets"]}, {"red", "green"})
        subprocess.run([*command, "--skip-existing"], check=True, capture_output=True)
        equal(
            len(json.loads(rcfp.request(ingested, "GET", "/datasets")[2])["datasets"]),
            2,
        )

    run("cli.ingest.skip-existing", cli_ingest)

    def cli_optimize():
        folder = out / "optimized"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "rcfp.cli",
                "optimize-rasters",
                str(files["red"]),
                "-o",
                str(folder),
                "--compression",
                "deflate",
            ],
            check=True,
            capture_output=True,
        )
        optimized = folder / "red.tif"
        check(optimized.exists(), "optimized raster missing")
        with Image.open(files["red"]) as original, Image.open(optimized) as result:
            equal(np.asarray(result).shape, np.asarray(original).shape)
            check(
                np.array_equal(np.asarray(result), np.asarray(original)),
                "optimization changed source pixels",
            )

    run("cli.optimize.lossless-pixels", cli_optimize)

    def cli_migrate_noop():
        result = subprocess.run(
            [sys.executable, "-m", "rcfp.cli", "migrate", str(database), "-y"],
            capture_output=True,
            text=True,
            check=True,
        )
        check(result.returncode == 0, "migration command failed")
        equal(json_response("/datasets")[0], 200)

    run("cli.migrate.current-schema", cli_migrate_noop)

    def package_identity():
        equal(rcfp.__protocol_version__, "0.10.1")
        check(rcfp.__version__, "package version missing")
        result = subprocess.run(
            [sys.executable, "-m", "rcfp.cli", "--version"],
            text=True,
            capture_output=True,
            check=True,
        )
        check(result.stdout.startswith("rcfp "), "CLI package identity changed")

    run("package.identity-version", package_identity)

    required = json.loads((ROOT / "tests/fixtures/contract-cases.json").read_text())[
        "required"
    ]
    equal([row["id"] for row in results], required, "required case inventory")
    check(len({row["id"] for row in results}) == len(results), "duplicate case ID")
    report = {
        "package_version": rcfp.__version__,
        "python": sys.version,
        "wheel_extension": str(rcfp._native.__file__),
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "fixture_manifest_sha256": hashlib.sha256(
            (CORPUS / "manifest.json").read_bytes()
        ).hexdigest(),
        "cases": results,
        "summary": {
            "passed": sum(row["status"] == "passed" for row in results),
            "failed": sum(row["status"] == "failed" for row in results),
            "total": len(results),
        },
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    suite = ET.Element(
        "testsuite",
        name="rcfp-contracts",
        tests=str(len(results)),
        failures=str(report["summary"]["failed"]),
    )
    for row in results:
        case = ET.SubElement(
            suite,
            "testcase",
            classname="contracts",
            name=row["id"],
            time=str(row["seconds"]),
        )
        if row["status"] == "failed":
            ET.SubElement(case, "failure", message=row["error"]).text = row["traceback"]
    ET.ElementTree(suite).write(
        out / "junit.xml", encoding="utf-8", xml_declaration=True
    )
    print(json.dumps(report["summary"]), flush=True)
    return int(report["summary"]["failed"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
