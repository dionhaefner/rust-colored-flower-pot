"""Exercise the packaged browser client against live catalog and raster routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"


def ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def ready(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except (urllib.error.URLError, TimeoutError):
            if process.poll() is not None:
                raise RuntimeError(f"service exited while starting: {url}") from None
            time.sleep(0.1)
    raise RuntimeError(f"service did not start: {url}")


def operation(binary: Path, folder: Path, request: dict) -> None:
    instruction = folder / "operation.json"
    instruction.write_text(json.dumps(request))
    subprocess.run(
        [str(binary), "data-operation", str(instruction)],
        check=True,
        capture_output=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    for row in manifest["fixtures"]:
        path = CORPUS / row["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise RuntimeError(f"fixture changed: {path}")
    database = out / "catalog.sqlite"
    operation(
        binary,
        out,
        {
            "operation": "create",
            "path": str(database),
            "keys": [
                {"key": "scene", "description": ""},
                {"key": "band", "description": ""},
            ],
        },
    )
    for band in ("red", "green", "blue"):
        operation(
            binary,
            out,
            {
                "operation": "insert",
                "path": str(database),
                "keys": ["lagoon", band],
                "raster": str(CORPUS / (band + ".tif")),
            },
        )
    backend_port, client_port = ports()
    log = (out / "services.log").open("w")
    backend = subprocess.Popen(
        [
            str(binary),
            "serve",
            "-d",
            str(database),
            "--bind",
            f"127.0.0.1:{backend_port}",
        ],
        stdout=log,
        stderr=log,
        env={**os.environ, "TC_RASTER_QUEUE_CAPACITY": "64"},
    )
    client = None
    try:
        ready(f"http://127.0.0.1:{backend_port}/keys", backend)
        client = subprocess.Popen(
            [
                str(binary),
                "connect",
                f"127.0.0.1:{backend_port}",
                "--port",
                str(client_port),
                "--no-browser",
            ],
            stdout=log,
            stderr=log,
        )
        ready(f"http://127.0.0.1:{client_port}/", client)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--use-angle=swiftshader",
                    "--enable-unsafe-swiftshader",
                ],
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            responses, errors = [], []
            page.on(
                "response",
                lambda response: (
                    responses.append({"url": response.url, "status": response.status, "body": response.text() if response.status >= 400 else ""})
                    if f"127.0.0.1:{backend_port}" in response.url
                    else None
                ),
            )
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route_request(route):
                url = route.request.url
                if url.startswith(("http://127.0.0.1:", "data:", "blob:")):
                    route.continue_()
                elif "api.mapbox.com/styles/v1/" in url:
                    route.fulfill(
                        json={
                            "version": 8,
                            "sources": {},
                            "layers": [
                                {
                                    "id": "offline-background",
                                    "type": "background",
                                    "paint": {"background-color": "#e9edf0"},
                                }
                            ],
                        }
                    )
                else:
                    route.abort()

            page.route("**/*", route_request)
            page.goto(f"http://127.0.0.1:{client_port}/")
            page.get_by_role("row").filter(has_text="lagoon").filter(
                has_text="red"
            ).first.click()
            page.wait_for_timeout(3500)
            page.screenshot(path=str(out / "singleband.png"))
            if not any(
                "/singleband/" in row["url"] and row["status"] == 200
                for row in responses
            ):
                raise AssertionError("browser did not request a singleband tile")
            if not any(
                "/metadata/" in row["url"] and row["status"] == 200 for row in responses
            ):
                raise AssertionError("browser did not request metadata")
            page.get_by_text("singleband", exact=True).click()
            page.get_by_role("option", name="rgb", exact=True).click()
            selects = page.locator('div[role="button"][aria-haspopup="listbox"]')
            for index, band in enumerate(("red", "green", "blue"), 1):
                selects.nth(index).click()
                page.get_by_role("option", name=band, exact=True).click()
            page.wait_for_timeout(3500)
            page.screenshot(path=str(out / "rgb.png"))
            if not any(
                "/rgb/" in row["url"] and row["status"] == 200 for row in responses
            ):
                raise AssertionError("browser did not request an RGB tile")
            with page.expect_popup() as opened:
                page.get_by_role("link", name="View full metadata").click()
            metadata = opened.value
            metadata.wait_for_load_state()
            data = json.loads(metadata.locator("pre").text_content())
            if "bounds" not in data or "percentiles" not in data:
                raise AssertionError("metadata popup omitted fields")
            (out / "metadata.json").write_text(json.dumps(data, indent=2) + "\n")
            failures = [row for row in responses if row["status"] >= 400]
            report = {
                "browser": browser.version,
                "responses": responses,
                "failed_responses": failures,
                "page_errors": errors,
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            }
            (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            if failures or errors:
                raise AssertionError(
                    f"browser failures: {failures}; page errors: {errors}"
                )
            browser.close()
    finally:
        if client is not None:
            client.terminate()
            client.wait(timeout=10)
        backend.terminate()
        backend.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    main()
