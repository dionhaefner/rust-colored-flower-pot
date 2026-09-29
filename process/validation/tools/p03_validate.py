"""Validate P03 bundle, Python/WSGI, Lambda adapter, and DB round trips."""

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import zipfile

import numpy as np
from PIL import Image, ImageDraw

from common import ROOT, read_json, write_json


REFERENCE_DB = ROOT / "process/validation/artifacts/capture-reproduction/reference.sqlite"
REFERENCE_PNG = ROOT / "process/validation/reference/capture/images/lagoon-red-preview.png"
RENDER_URL = "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D"
EVENT = {
    "version": "2.0",
    "rawPath": "/singleband/lagoon/red/preview.png",
    "rawQueryString": "stretch_range=%5B0%2C3000%5D",
    "requestContext": {"http": {"method": "GET"}},
}


def decoded(png):
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def check(condition, label, checks):
    checks.append({"name": label, "passed": bool(condition)})
    if not condition:
        raise AssertionError(label)


def frozen_python_read(database):
    script = """
import json, sys
import terracotta
driver = terracotta.get_driver(sys.argv[1], provider='sqlite')
with driver.connect():
    datasets = driver.get_datasets()
    metadata = driver.get_metadata(('lagoon', 'red'))
print(json.dumps({
    'keys': list(driver.key_names),
    'datasets': [{'keys': list(keys), 'path': path} for keys, path in datasets.items()],
    'bounds': list(metadata['bounds']),
    'percentile_count': len(metadata['percentiles']),
    'metadata': metadata['metadata'],
}))
"""
    result = subprocess.run(
        [str(ROOT / ".venv-reference/bin/python"), "-c", script, str(database)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def runtime_smoke(lambda_zip, database, out):
    extracted = out / "lambda-package"
    with zipfile.ZipFile(lambda_zip) as archive:
        archive.extractall(extracted)
        names = archive.namelist()
        bootstrap_mode = archive.getinfo("bootstrap").external_attr >> 16
        executable_mode = archive.getinfo("runtime/bin/terracotta").external_attr >> 16
    if not bootstrap_mode & 0o111:
        raise AssertionError("Lambda bootstrap is not executable in the zip metadata")
    (extracted / "bootstrap").chmod(bootstrap_mode & 0o777)
    if not executable_mode & 0o111:
        raise AssertionError("Lambda executable is not executable in the zip metadata")
    (extracted / "runtime/bin/terracotta").chmod(executable_mode & 0o777)
    state = {"event_sent": False, "response": None, "response_path": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if not state["event_sent"]:
                state["event_sent"] = True
                body = json.dumps(EVENT).encode()
                self.send_response(200)
                self.send_header("Lambda-Runtime-Aws-Request-Id", "p03-request")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            state["response"] = self.rfile.read(length)
            state["response_path"] = self.path
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = dict(
        os.environ,
        TC_DRIVER_PATH=str(database),
        TC_RASTER_WORKERS="1",
        AWS_LAMBDA_RUNTIME_API=f"127.0.0.1:{server.server_address[1]}",
    )
    try:
        process = subprocess.run(
            [str(extracted / "bootstrap")],
            cwd=extracted,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    if state["response"] is None:
        raise AssertionError(f"runtime produced no response: {process.stderr}")
    return {
        "zip_entries": names,
        "process_exit": process.returncode,
        "process_stderr": process.stderr,
        "response_path": state["response_path"],
        "response": json.loads(state["response"]),
    }


def run(bundle, python_package, lambda_zip, out):
    if out.exists():
        raise FileExistsError("choose a fresh P03 validation output directory")
    out.mkdir(parents=True)
    (out / "images").mkdir()
    checks = []
    golden = REFERENCE_PNG.read_bytes()
    golden_array = decoded(golden)

    environment = dict(os.environ)
    for key in ("LD_LIBRARY_PATH", "TERRACOTTA_GDAL_LIBRARY", "GDAL_DATA", "PROJ_DATA"):
        environment.pop(key, None)
    version = subprocess.run(
        [str(bundle / "bin/terracotta"), "--version-json"],
        cwd=out,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    identity = json.loads(version.stdout)
    check(identity["phase"] == "P03", "bundle explicit version identities", checks)
    check((bundle / "licenses/TERRACOTTA-LICENSE.txt").is_file(), "upstream notice bundled", checks)
    check((bundle / "licenses/RASTERIO-LICENSE.txt").is_file(), "Rasterio notice bundled", checks)

    sys.path.insert(0, str(python_package))
    import terracotta_rust
    from terracotta_rust.wsgi import create_app

    native = terracotta_rust.render_preview(
        REFERENCE_DB, ["lagoon", "red"], stretch_range=(0, 3000)
    )
    (out / "images/python-extension.png").write_bytes(native)
    check(np.array_equal(decoded(native), golden_array), "Python extension decoded image exact", checks)
    check(
        terracotta_rust.identity(REFERENCE_DB) == identity,
        "Python identity matches executable",
        checks,
    )
    try:
        terracotta_rust.render_preview(REFERENCE_DB, ["missing", "red"])
    except terracotta_rust.TerracottaError as error:
        check("HTTP 404" in str(error), "Python render error translated", checks)
    else:
        check(False, "Python render error translated", checks)

    copied = out / "rust-written.sqlite"
    terracotta_rust.copy_dataset(REFERENCE_DB, copied, ["lagoon", "red"])
    # Distribution adapters run outside the repository working directory, so
    # relocate the retained relative raster path to the same pinned real file.
    with sqlite3.connect(copied) as connection:
        connection.execute(
            "UPDATE datasets SET path = ?",
            (str((ROOT / ".cache/corpus/subsets/lagoon-red.tif").resolve()),),
        )
    python_read = frozen_python_read(copied)
    check(python_read["keys"] == ["scene", "band"], "Python reads Rust key order", checks)
    check(python_read["percentile_count"] == 99, "Python reads Rust float32 metadata", checks)
    check(len(python_read["datasets"]) == 1, "Python reads Rust dataset row", checks)
    copied_png = terracotta_rust.render_preview(
        copied, ["lagoon", "red"], stretch_range=(0, 3000)
    )
    check(np.array_equal(decoded(copied_png), golden_array), "Rust-written DB renders exact", checks)

    wsgi_status = []
    wsgi_headers = []

    def start_response(status, headers):
        wsgi_status.append(status)
        wsgi_headers.extend(headers)

    wsgi_body = b"".join(
        create_app(str(REFERENCE_DB))(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/singleband/lagoon/red/preview.png",
                "QUERY_STRING": "stretch_range=%5B0%2C3000%5D",
            },
            start_response,
        )
    )
    (out / "images/wsgi.png").write_bytes(wsgi_body)
    check(wsgi_status == ["200 OK"], "WSGI status", checks)
    check(np.array_equal(decoded(wsgi_body), golden_array), "WSGI decoded image exact", checks)

    event_path = out / "event.json"
    write_json(event_path, EVENT)
    event_result = subprocess.run(
        [
            str(bundle / "bin/terracotta"), "lambda-event", "-d", str(copied),
            "--event", str(event_path),
        ],
        cwd=out,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    lambda_response = json.loads(event_result.stdout)
    lambda_png = base64.b64decode(lambda_response["body"])
    (out / "images/lambda-event.png").write_bytes(lambda_png)
    check(lambda_response["isBase64Encoded"], "Lambda image response is base64", checks)
    check(np.array_equal(decoded(lambda_png), golden_array), "Lambda event decoded image exact", checks)

    v1_event_path = out / "event-v1.json"
    write_json(v1_event_path, {
        "httpMethod": "GET",
        "path": "/keys",
        "queryStringParameters": None,
    })
    v1_result = subprocess.run(
        [
            str(bundle / "bin/terracotta"), "lambda-event", "-d", str(copied),
            "--event", str(v1_event_path),
        ],
        cwd=out,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    v1_response = json.loads(v1_result.stdout)
    check(v1_response["statusCode"] == 200, "API Gateway v1 status", checks)
    check(not v1_response["isBase64Encoded"], "API Gateway v1 JSON is not base64", checks)
    check(json.loads(v1_response["body"])["keys"][0]["key"] == "scene", "API Gateway v1 body", checks)

    runtime = runtime_smoke(lambda_zip, copied, out)
    runtime_png = base64.b64decode(runtime["response"]["body"])
    check(
        runtime["response_path"].endswith("/p03-request/response"),
        "Lambda Runtime API response path",
        checks,
    )
    check(np.array_equal(decoded(runtime_png), golden_array), "Lambda package runtime decoded image exact", checks)
    check(runtime["process_exit"] == 2, "Lambda runtime exits on injected next failure", checks)
    check("bootstrap" in runtime["zip_entries"], "Lambda zip bootstrap present", checks)

    sheet = Image.new("RGB", (512, 3 * 280), "white")
    draw = ImageDraw.Draw(sheet)
    for row, (name, candidate) in enumerate(
        [("Python extension", native), ("WSGI", wsgi_body), ("Lambda adapter", lambda_png)]
    ):
        reference_image = Image.open(io.BytesIO(golden)).convert("RGBA")
        candidate_image = Image.open(io.BytesIO(candidate)).convert("RGBA")
        sheet.paste(reference_image, (0, row * 280), reference_image)
        sheet.paste(candidate_image, (256, row * 280), candidate_image)
        draw.text((4, row * 280 + 258), f"Python reference | Rust {name}", fill="black")
    sheet.save(out / "side-by-side.png")

    report = {
        "passed": all(item["passed"] for item in checks),
        "reference_commit": read_json(ROOT / "process/validation/reference/capture/capture.json")["reference_commit"],
        "identity": identity,
        "checks": checks,
        "summary": {"passed": sum(item["passed"] for item in checks), "total": len(checks)},
        "artifacts": {
            "bundle_bytes": sum(path.stat().st_size for path in bundle.rglob("*") if path.is_file()),
            "lambda_zip_bytes": lambda_zip.stat().st_size,
            "lambda_zip_sha256": hashlib.sha256(lambda_zip.read_bytes()).hexdigest(),
        },
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "libc": platform.libc_ver(),
        },
        "runtime": {
            "response_path": runtime["response_path"],
            "controlled_exit": runtime["process_exit"],
        },
        "cross_language": {
            "python_to_rust": "Frozen-Python-created database rendered through the Rust CPython extension.",
            "rust_to_python": python_read,
        },
        "comparison": "Exact decoded RGBA against the frozen real-raster reference; PNG bytes are not compared.",
        "visual_check": "side-by-side.png contains three native-scale reference/adapter pairs.",
    }
    write_json(out / "report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "process/validation/artifacts/p03-bundle")
    parser.add_argument("--python-package", type=Path, default=ROOT / "process/validation/artifacts/p03-python")
    parser.add_argument("--lambda-zip", type=Path, default=ROOT / "process/validation/artifacts/p03-lambda/terracotta-lambda.zip")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.bundle.resolve(), args.python_package.resolve(), args.lambda_zip.resolve(), args.out.resolve())
