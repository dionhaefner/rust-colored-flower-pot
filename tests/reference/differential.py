"""Compare the native HTTP server with the pinned Terracotta release on identical data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "fixtures" / "corpus"


def cases():
    def item(name, url, method="GET", body=None, headers=None):
        return {
            "id": name,
            "url": url,
            "method": method,
            "body": body,
            "headers": headers or {},
        }

    single = "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D"
    rgb = "/rgb/lagoon/preview.png?r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D"
    palette = {
        10: [1, 2, 3, 255],
        20: [4, 5, 6, 255],
        30: [7, 8, 9, 255],
        40: [10, 11, 12, 255],
        50: [13, 14, 15, 255],
        60: [16, 17, 18, 255],
        80: [19, 20, 21, 255],
        90: [22, 23, 24, 255],
    }
    return [
        item("keys", "/keys"),
        item("datasets", "/datasets"),
        item("datasets-filter", "/datasets?scene=lagoon"),
        item("datasets-multivalue", "/datasets?band=%5Bred%2Cblue%5D"),
        item("datasets-page", "/datasets?limit=2&page=1"),
        item("datasets-invalid-page", "/datasets?page=bad"),
        item("metadata-red", "/metadata/lagoon/red"),
        item("metadata-columns", "/metadata/lagoon/red?columns=%5B%22range%22%5D"),
        item("metadata-missing", "/metadata/lagoon/missing"),
        item(
            "metadata-batch",
            "/metadata",
            "POST",
            {"keys": [["lagoon", "red"], ["lagoon", "green"]]},
            {"content-type": "application/json"},
        ),
        item(
            "colormap",
            "/colormap?colormap=viridis&stretch_range=%5B0%2C1%5D&num_values=3",
        ),
        item("singleband", single),
        item("singleband-size", single + "&tile_size=%5B96%2C64%5D"),
        item("singleband-invalid", single + "&tile_size=bad"),
        item("rgb", rgb),
        item("rgb-swap", rgb.replace("r=red&g=green&b=blue", "r=blue&g=green&b=red")),
        item("rgb-invalid", "/rgb/lagoon/preview.png?r=red&g=green"),
        item(
            "compute-ndvi",
            "/compute/lagoon/preview.png?expression=%28v1-v2%29%2F%28v1%2Bv2%29&v1=nir&v2=red&stretch_range=%5B-1%2C1%5D",
        ),
        item(
            "compute-identity",
            "/compute/lagoon/preview.png?expression=v1&v1=red&stretch_range=%5B0%2C3000%5D",
        ),
        item("compute-invalid", "/compute/lagoon/preview.png?expression=v1%2B&v1=red"),
        item(
            "categories",
            "/singleband/lagoon/classes/preview.png?colormap=explicit&explicit_color_map="
            + quote(json.dumps(palette), safe=""),
        ),
        item(
            "mask-edge", "/singleband/edge/red/preview.png?stretch_range=%5B0%2C3000%5D"
        ),
        item("geographic-dem", "/singleband/dolomites/elevation/preview.png"),
        item("tile-outside", "/singleband/lagoon/red/0/0/0.png"),
        item("cors", "/keys", "OPTIONS", headers={"origin": "https://example.com"}),
        item("swagger", "/swagger.json"),
    ]


def capture(mode, database, out, binary=None):
    out.mkdir(parents=True, exist_ok=False)
    server = None
    rows = []
    try:
        if mode == "reference":
            import terracotta as tc
            from terracotta.server import create_app

            tc.update_settings(DRIVER_PATH=str(database))
            app = create_app()
            app.logger.disabled = True
            client = app.test_client()
        else:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            with (out / "server.log").open("w") as logfile:
                server = subprocess.Popen(
                    [
                        str(binary),
                        "serve",
                        "-d",
                        str(database),
                        "--bind",
                        f"127.0.0.1:{port}",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=logfile,
                )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/keys", timeout=1
                    ).close()
                    break
                except (urllib.error.URLError, TimeoutError):
                    if server.poll() is not None:
                        raise RuntimeError("candidate server exited early")
                    time.sleep(0.1)
            else:
                raise RuntimeError("candidate server did not become ready")
        for index, case in enumerate(cases()):
            data = None if case["body"] is None else json.dumps(case["body"]).encode()
            if mode == "reference":
                result = client.open(
                    case["url"],
                    method=case["method"],
                    data=data,
                    headers=case["headers"],
                )
                status, headers, body = result.status_code, result.headers, result.data
            else:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}" + case["url"],
                    data=data,
                    method=case["method"],
                    headers=case["headers"],
                )
                try:
                    result = urllib.request.urlopen(request, timeout=60)
                except urllib.error.HTTPError as error:
                    result = error
                with result:
                    status, headers, body = result.status, result.headers, result.read()
            kind = headers.get("Content-Type", "")
            row = {
                "id": case["id"],
                "status": status,
                "content_type": kind,
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }
            if kind.startswith("application/json"):
                row["json"] = json.loads(body)
            elif kind.startswith("image/png"):
                name = f"{index:03d}-{case['id']}.png"
                (out / name).write_bytes(body)
                row["png"] = name
            elif status < 400:
                row["text"] = body.decode(errors="replace")
            if case["id"] == "cors":
                row["cors"] = headers.get("Access-Control-Allow-Origin")
            rows.append(row)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    (out / "capture.json").write_text(json.dumps(rows, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--reference-python", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--capture-mode", choices=["reference", "candidate"])
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    if args.capture_mode:
        capture(
            args.capture_mode,
            args.database.resolve(),
            args.out.resolve(),
            args.binary.resolve() if args.binary else None,
        )
        return 0
    if not args.binary or not args.reference_python:
        parser.error("--binary and --reference-python required")
    import numpy as np
    from PIL import Image

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    reference_python = args.reference_python.absolute()
    database = out / "catalog.sqlite"
    fixture_manifest = json.loads((CORPUS / "manifest.json").read_text())
    for row in fixture_manifest["fixtures"]:
        path = CORPUS / row["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise RuntimeError(f"fixture checksum mismatch: {path}")

    def operation(request):
        instruction = out / "operation.json"
        instruction.write_text(json.dumps(request))
        result = subprocess.run(
            [str(binary), "data-operation", str(instruction)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        value = json.loads(result.stdout)
        if isinstance(value, dict) and "error" in value:
            raise RuntimeError(value["error"])

    operation(
        {
            "operation": "create",
            "path": str(database),
            "keys": [
                {"key": "scene", "description": "Location"},
                {"key": "band", "description": "Spectral band"},
            ],
        }
    )
    for scene, band, name in [
        ("lagoon", "red", "red"),
        ("lagoon", "green", "green"),
        ("lagoon", "blue", "blue"),
        ("lagoon", "nir", "nir"),
        ("lagoon", "classes", "classes"),
        ("dolomites", "elevation", "elevation"),
        ("edge", "red", "edge"),
    ]:
        operation(
            {
                "operation": "insert",
                "path": str(database),
                "keys": [scene, band],
                "raster": str((CORPUS / (name + ".tif")).resolve()),
            }
        )
    reference = out / "reference"
    candidate = out / "candidate"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / ".cache" / "reference")
    subprocess.run(
        [
            str(reference_python),
            str(Path(__file__).resolve()),
            "--capture-mode",
            "reference",
            "--database",
            str(database),
            "--out",
            str(reference),
        ],
        env=env,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--capture-mode",
            "candidate",
            "--database",
            str(database),
            "--out",
            str(candidate),
            "--binary",
            str(binary),
        ],
        check=True,
    )
    expected = json.loads((reference / "capture.json").read_text())
    actual = json.loads((candidate / "capture.json").read_text())
    if [row["id"] for row in expected] != [row["id"] for row in actual]:
        raise RuntimeError("differential case IDs differ")
    reports = []
    for left, right in zip(expected, actual, strict=True):
        errors = []
        for key in ("status", "content_type", "cors", "text"):
            if left.get(key) != right.get(key):
                errors.append(f"{key} differs")
        comparison = "exact"
        if left.get("json") != right.get("json"):
            if left["id"] in {
                "datasets-invalid-page",
                "rgb-invalid",
                "compute-invalid",
            }:
                meaning = {
                    "datasets-invalid-page": ("page",),
                    "rgb-invalid": ("b", "missing"),
                    "compute-invalid": ("stretch_range", "missing"),
                }[left["id"]]
                message = right.get("json", {}).get("message", "").lower()
                if all(word in message for word in meaning) and isinstance(
                    left.get("json", {}).get("message"), str
                ):
                    comparison = "error-meaning"
                else:
                    errors.append("error meaning differs")
            elif left["id"] == "swagger":
                original = json.loads(json.dumps(left["json"]))
                original["info"]["title"] = "RCFP"
                candidate_spec = json.loads(json.dumps(right["json"]))
                native = os.environ.get("RCFP_COMPAT_PROFILE", "native") == "native"
                if native:
                    if (
                        candidate_spec["info"]["description"]
                        != "RCFP raster tile service."
                    ):
                        errors.append("native service description differs")
                    candidate_spec["info"]["description"] = original["info"][
                        "description"
                    ]
                    for route in (
                        "/layers",
                        "/layers/{id}",
                        "/layers/{id}/render",
                        "/layers/{id}/render.png",
                    ):
                        if route not in candidate_spec["paths"]:
                            errors.append(f"native route missing: {route}")
                        candidate_spec["paths"].pop(route, None)
                    candidate_spec.get("components", {}).get("schemas", {}).pop(
                        "NativeRenderSpec", None
                    )
                    for route in (
                        "/layers/{id}/plan",
                        "/tilesets",
                        "/tileMatrixSets",
                        "/tileMatrixSets/{id}",
                        "/tilejson/{id}.json",
                        "/tiles/{id}/{tileMatrixSet}/{z}/{x}/{y}.png",
                        "/point/{id}",
                    ):
                        candidate_spec["paths"].pop(route, None)
                    for schema in (
                        "Tilesets",
                        "TileMatrixSets",
                        "TileMatrixSet",
                        "TileJSON",
                        "PointInspection",
                    ):
                        candidate_spec.get("components", {}).get("schemas", {}).pop(
                            schema, None
                        )
                    batch = candidate_spec["paths"].pop("/metadata", {})
                    if "post" not in batch:
                        errors.append("native batch metadata route missing")
                    for path, entry in candidate_spec["paths"].items():
                        if not path.startswith(("/rgb/", "/singleband/", "/compute/")):
                            continue
                        names = {"revision"}
                        for status in ("304", "409", "412"):
                            entry["get"]["responses"].pop(status, None)
                        headers = entry["get"]["responses"]["200"].get("headers", {})
                        for name in ("ETag", "X-RCFP-Revision", "Cache-Control"):
                            headers.pop(name, None)
                        if not headers:
                            entry["get"]["responses"]["200"].pop("headers", None)
                        if not path.endswith("preview.png") and not path.startswith(
                            "/compute/"
                        ):
                            names.update(
                                ("r_band", "g_band", "b_band")
                                if path.startswith("/rgb/")
                                else ("band",)
                            )
                        if path.startswith("/rgb/"):
                            names.update(("format", "quality"))
                            entry["get"]["responses"]["200"].pop("content", None)
                        entry["get"]["parameters"] = [
                            p
                            for p in entry["get"]["parameters"]
                            if p["name"] not in names
                        ]
                if original == candidate_spec:
                    comparison = "package-title"
                else:
                    errors.append("swagger differs beyond title")
            else:
                errors.append("json differs")
        if "png" in left and "png" in right:
            with Image.open(reference / left["png"]) as img:
                a = np.asarray(img.convert("RGBA"))
            with Image.open(candidate / right["png"]) as img:
                b = np.asarray(img.convert("RGBA"))
            if a.shape != b.shape:
                errors.append(f"image shape differs: {a.shape} vs {b.shape}")
            elif not np.array_equal(a, b):
                errors.append(
                    f"decoded image differs: {np.count_nonzero(a != b)} channels"
                )
        elif ("png" in left) != ("png" in right):
            errors.append("image response presence differs")
        reports.append(
            {
                "id": left["id"],
                "status": "failed" if errors else "passed",
                "comparison": comparison,
                "errors": errors,
            }
        )
    report = {
        "reference_source_sha256": "e5d2ac9b78a36d0f28414b1a4890c961dede50d1f9ce33b8d23eee643f5e2348",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "fixture_manifest_sha256": hashlib.sha256(
            (CORPUS / "manifest.json").read_bytes()
        ).hexdigest(),
        "cases": reports,
        "passed": sum(row["status"] == "passed" for row in reports),
        "failed": sum(row["status"] == "failed" for row in reports),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "failed": report["failed"]}))
    for row in reports:
        if row["errors"]:
            print(row["id"], row["errors"])
    return int(report["failed"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
