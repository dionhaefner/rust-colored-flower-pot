"""Check the demo catalog, Lambda adapter, CORS and Pages viewer in Chromium."""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import importlib.util
import io
import json
import os
import queue
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.request
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def helper(name):
    path = ROOT / "packaging/demo" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def server(handler):
    service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    return service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--binary", type=Path)
    mode.add_argument("--image")
    parser.add_argument("--remote-catalog", action="store_true")
    parser.add_argument(
        "--sources", type=Path, help="Use previously downloaded rasters"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    process = None
    services = []
    invoke_lock = threading.Lock()
    checks = []
    log = (out / "runtime.log").open("w")
    try:
        if args.binary:
            binary = args.binary.resolve()
            prepared = out / "prepared"
            manifest = helper("prepare").prepare(binary, prepared, sources=args.sources)
            database = prepared / "catalog.sqlite"
            events, replies = queue.Queue(), queue.Queue()

            class Runtime(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_GET(self):
                    event = events.get()
                    data = json.dumps(event).encode()
                    self.send_response(200)
                    self.send_header(
                        "Lambda-Runtime-Aws-Request-Id", "demo-test-request"
                    )
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def do_POST(self):
                    value = json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    )
                    replies.put(value)
                    self.send_response(202)
                    self.end_headers()

            runtime = server(Runtime)
            services.append(runtime)

            def invoke(event):
                with invoke_lock:
                    events.put(event)
                    return replies.get(timeout=30)

        else:
            container = subprocess.check_output(
                ["docker", "create", args.image], text=True
            ).strip()
            try:
                for name in ("catalog.sqlite", "manifest.json"):
                    subprocess.run(
                        [
                            "docker",
                            "cp",
                            f"{container}:/var/task/{name}",
                            str(out / name),
                        ],
                        check=True,
                    )
            finally:
                subprocess.run(["docker", "rm", container], check=True)
            database = out / "catalog.sqlite"
            manifest = json.loads((out / "manifest.json").read_text())
            assert (
                hashlib.sha256(database.read_bytes()).hexdigest()
                == manifest["catalog_sha256"]
            )
            with sqlite3.connect(database) as connection:
                paths = [
                    row[0] for row in connection.execute("SELECT path FROM datasets")
                ]
                assert all(
                    path.startswith(
                        "s3://" if args.remote_catalog else "/var/task/data/"
                    )
                    for path in paths
                )
                for row in manifest["fixtures"]:
                    connection.execute(
                        "UPDATE datasets SET path = ? WHERE scene = ? AND band = ?",
                        (f"/var/task/data/{row['file']}", row["scene"], row["band"]),
                    )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]

            def invoke(event):
                with invoke_lock:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/2015-03-31/functions/function/invocations",
                        data=json.dumps(event).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(request, timeout=30) as response:
                        return json.load(response)

        with sqlite3.connect(database) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM metadata").fetchone()[0] == 6
            )
        assert {(row["scene"], row["band"]) for row in manifest["fixtures"]} == {
            ("lagoon", "red"),
            ("lagoon", "green"),
            ("lagoon", "blue"),
            ("lagoon", "nir"),
            ("dolomites", "elevation"),
            ("cape-town", "elevation"),
        }
        checks.append("pinned-assets-and-precomputed-catalog")

        class Api(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def dispatch(self):
                url = urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                response = invoke(
                    {
                        "version": "2.0",
                        "rawPath": url.path,
                        "rawQueryString": url.query,
                        "requestContext": {"http": {"method": self.command}},
                        "headers": dict(self.headers),
                        "body": base64.b64encode(body).decode(),
                        "isBase64Encoded": True,
                    }
                )
                data = (
                    base64.b64decode(response["body"])
                    if response["isBase64Encoded"]
                    else response["body"].encode()
                )
                self.send_response(response["statusCode"])
                for key, value in response["headers"].items():
                    if key.lower() != "content-length":
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_OPTIONS = dispatch

        api = server(Api)
        services.append(api)
        site_root = out / "site"
        site_root.mkdir()
        static = server(
            functools.partial(SimpleHTTPRequestHandler, directory=str(site_root))
        )
        services.append(static)
        origin = f"http://127.0.0.1:{static.server_port}"
        allowed = json.dumps([origin.replace(".", "\\.") + "$"])
        env = {
            **os.environ,
            "TC_DRIVER_PATH": str(database),
            "TC_COMPAT_PROFILE": "native",
            "TC_ALLOWED_ORIGINS_METADATA": allowed,
            "TC_ALLOWED_ORIGINS_TILES": allowed,
            "TC_RASTER_WORKERS": "1",
            "TC_RASTER_QUEUE_CAPACITY": "4",
        }
        if args.binary:
            env["AWS_LAMBDA_RUNTIME_API"] = f"127.0.0.1:{runtime.server_port}"
            env["TERRACOTTA_RUNTIME_ROOT"] = str(binary.parent.parent)
            process = subprocess.Popen(
                [str(binary), "lambda-runtime"], env=env, stdout=log, stderr=log
            )
        else:
            process = subprocess.Popen(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    f"rcfp-demo-test-{os.getpid()}",
                    "--platform",
                    "linux/amd64",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,size=512m",
                    "-p",
                    f"127.0.0.1:{port}:8080",
                    "-v",
                    f"{database}:/var/task/catalog.sqlite:ro",
                    "-e",
                    f"TC_ALLOWED_ORIGINS_METADATA={allowed}",
                    "-e",
                    f"TC_ALLOWED_ORIGINS_TILES={allowed}",
                    args.image,
                ],
                stdout=log,
                stderr=log,
            )
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError("Lambda image exited; inspect runtime.log")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Lambda runtime emulator did not listen")

        def event(path, method="GET", headers=None):
            url = urlsplit(path)
            return invoke(
                {
                    "version": "2.0",
                    "rawPath": url.path,
                    "rawQueryString": url.query,
                    "requestContext": {"http": {"method": method}},
                    "headers": headers or {"Origin": origin},
                }
            )

        assert (
            json.loads(event("/__terracotta/identity")["body"])["compatibility_profile"]
            == "native"
        )
        assert len(json.loads(event("/datasets")["body"])["datasets"]) == 6
        preview = "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D"
        for path in (
            preview,
            "/singleband/dolomites/elevation/preview.png",
            "/singleband/cape-town/elevation/preview.png",
        ):
            image_response = event(path)
            assert (
                image_response["statusCode"] == 200
                and image_response["isBase64Encoded"]
            )
            png = base64.b64decode(image_response["body"])
            with Image.open(io.BytesIO(png)) as image:
                assert image.convert("RGBA").getchannel("A").getextrema()[1] > 0
                low, high = image.convert("L").getextrema()
                assert low < high
            (out / (path.split("/")[2] + ".png")).write_bytes(png)
        response = event(preview)
        assert response["headers"]["access-control-allow-origin"] == origin
        assert (
            "access-control-allow-origin"
            not in event(preview, headers={"Origin": origin + ".invalid"})["headers"]
        )
        assert (
            event(
                "/keys",
                "OPTIONS",
                {"Origin": origin, "Access-Control-Request-Method": "GET"},
            )["statusCode"]
            == 200
        )
        assert event("/singleband/missing/red/preview.png")["statusCode"] == 404
        checks.append("persistent-runtime-decodes-all-scenes-and-enforces-cors")

        demo = site_root / "project/demo"
        helper("export-viewer").export(
            demo, f"http://127.0.0.1:{api.server_port}", "demo-fixtures"
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors, responses = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "response",
                    lambda response: responses.append((response.url, response.status)),
                )
                page.goto(origin + "/project/demo/", wait_until="networkidle")
                expect(page.locator(".leaflet-control-attribution")).to_contain_text(
                    "Contains modified Copernicus Sentinel data 2021"
                )
                expect(page.locator(".layer-row")).to_have_count(4)
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                expect(page.locator("#mode")).to_have_value("rgb")
                page.get_by_label("Scene", exact=True).select_option("dolomites")
                page.wait_for_function(
                    "document.querySelector('#workspace').getAttribute('aria-busy') === 'false'"
                )
                expect(page.locator("#mode")).to_have_value("singleband")
                expect(page.locator(".leaflet-control-attribution")).to_contain_text(
                    "Copernicus WorldDEM-30"
                )
                expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
                expect(page.locator("#share")).to_be_hidden()
                expect(page.get_by_label("Basemap")).to_have_value("land")
                page.goto(
                    origin + "/project/demo/examples/leaflet.html",
                    wait_until="networkidle",
                )
                assert not errors, errors
                assert not [row for row in responses if row[1] >= 400], responses
                page.screenshot(path=str(out / "viewer.png"))
            finally:
                browser.close()
        checks.append("pages-project-path-viewer-rgb-basemap-sdk-example")
        (out / "report.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "checks": checks,
                    "runtime": "official-image-emulator"
                    if args.image
                    else "native-runtime-api-adapter",
                    "aws_service_invocation": False,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        if args.image and process:
            subprocess.run(
                ["docker", "stop", f"rcfp-demo-test-{os.getpid()}"], check=False
            )
        if process:
            process.terminate()
            process.wait(timeout=15)
        for service in reversed(services):
            service.shutdown()
            service.server_close()
        log.close()


if __name__ == "__main__":
    main()
