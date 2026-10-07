"""Check and time viewport requests against an existing HTTP deployment."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import math
import platform
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def sample_workload():
    zoom = 13
    x = int((12.358 + 180) / 360 * 2**zoom)
    y = int((1 - math.asinh(math.tan(math.radians(45.464))) / math.pi) / 2 * 2**zoom)

    def viewport(dx, z=zoom):
        scale = 2 ** (z - zoom)
        return [
            f"/singleband/lagoon/red/{z}/{x * scale + dx + a}/{y * scale + b}.png?stretch_range=%5B0%2C3000%5D"
            for a in (0, 1)
            for b in (0, 1)
        ]

    manifest = json.loads((ROOT / "tests/fixtures/demo/manifest.json").read_text())
    return {
        "scope": "small bundled crops; deployment smoke only",
        "stages": [
            {"name": "viewport", "paths": viewport(0)},
            {"name": "pan", "paths": viewport(1)},
            {"name": "zoom", "paths": viewport(0, zoom + 1)},
            {"name": "revisit", "paths": viewport(0)},
        ],
        "references": [
            {
                "path": "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D",
                "file": str(ROOT / "tests/fixtures/demo/preview.png"),
                "sha256": manifest["preview_sha256"],
            }
        ],
    }


def fetch(origin, path, images):
    started = time.perf_counter()
    row = {"path": path}
    try:
        with urlopen(origin + path, timeout=60) as response:
            body = response.read()
            row.update(status=response.status, bytes=len(body))
        row["http_ms"] = (time.perf_counter() - started) * 1000
        image = Image.open(io.BytesIO(body)).convert("RGBA")
        row.update(
            dimensions=list(image.size),
            rgba_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
        )
        (images / (hashlib.sha256(path.encode()).hexdigest() + ".png")).write_bytes(
            body
        )
    except (HTTPError, URLError, OSError, ValueError) as exc:
        row.update(
            status=getattr(exc, "code", row.get("status", 0)),
            error=str(exc),
            http_ms=(time.perf_counter() - started) * 1000,
        )
    return row


def browser_check(binary, origin, out):
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with (out / "viewer.log").open("w") as log:
        process = subprocess.Popen(
            [str(binary), "connect", origin, "--no-browser", "--port", str(port)],
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=1):
                        break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError("viewer exited; inspect viewer.log")
                    time.sleep(0.1)
            else:
                raise TimeoutError("viewer did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True, args=["--no-sandbox"]
                )
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    errors, failed = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on(
                        "response",
                        lambda response: (
                            failed.append(
                                {"url": response.url, "status": response.status}
                            )
                            if response.status >= 400
                            else None
                        ),
                    )
                    timings = {}
                    started = time.perf_counter()
                    page.goto(
                        f"http://127.0.0.1:{port}/", wait_until="domcontentloaded"
                    )
                    expect(page.locator(".leaflet-tile-loaded").first).to_be_visible(
                        timeout=60000
                    )
                    page.wait_for_function(
                        "[...document.querySelectorAll('.leaflet-tile')].every(i => i.complete && i.naturalWidth > 0)"
                    )
                    timings["first_view_ms"] = (time.perf_counter() - started) * 1000
                    for name in ("pan", "zoom", "revisit"):
                        previous = page.locator(".leaflet-tile").evaluate_all(
                            "images => images.map(i => i.src)"
                        )
                        transform = page.locator(".leaflet-map-pane").evaluate(
                            "element => element.style.transform"
                        )
                        started = time.perf_counter()
                        if name == "zoom":
                            page.locator(".leaflet-control-zoom-in").click()
                        else:
                            if name == "revisit":
                                page.locator(".leaflet-control-zoom-out").click()
                            page.locator("#map").focus()
                            for _ in range(3):
                                page.keyboard.press(
                                    "ArrowRight" if name == "pan" else "ArrowLeft"
                                )
                        if name == "zoom":
                            page.wait_for_function(
                                "old => [...document.querySelectorAll('.leaflet-tile-loaded')].some(i => !old.includes(i.src) && i.complete && i.naturalWidth > 0)",
                                arg=previous,
                                timeout=60000,
                            )
                        else:
                            page.wait_for_function(
                                "old => document.querySelector('.leaflet-map-pane').style.transform !== old && !document.querySelector('.leaflet-pan-anim')",
                                arg=transform,
                            )
                        page.wait_for_function(
                            "!document.querySelector('.leaflet-zoom-anim') && document.querySelectorAll('.leaflet-tile').length > 0 && [...document.querySelectorAll('.leaflet-tile')].every(i => i.complete && i.naturalWidth > 0)"
                        )
                        timings[name + "_ms"] = (time.perf_counter() - started) * 1000
                    page.screenshot(path=str(out / "viewer.png"))
                    result = {
                        "timings": timings,
                        "javascript_errors": errors,
                        "http_errors": failed,
                        "passed": not errors and not failed,
                    }
                    (out / "browser.json").write_text(
                        json.dumps(result, indent=2) + "\n"
                    )
                    return result
                finally:
                    browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def run(origin, workload, out, concurrency, repeats, viewer_binary=None):
    images = out / "images"
    images.mkdir()
    report = {
        "origin": origin,
        "client_platform": platform.platform(),
        "concurrency": concurrency,
        "repeats": repeats,
        "scope": workload.get("scope", "operator-supplied deployment workload"),
        "workload": workload,
        "browser": "not checked",
        "passed": False,
    }
    rows = []
    summaries = []
    try:
        for path, name in (("/keys", "keys"), ("/__terracotta/identity", "identity")):
            with urlopen(origin + path, timeout=30) as response:
                report[name] = json.load(response)
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as clients:
            for trial in range(repeats):
                for stage in workload["stages"]:
                    started = time.perf_counter()
                    batch = list(
                        clients.map(
                            lambda path: fetch(origin, path, images), stage["paths"]
                        )
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    for row in batch:
                        row.update(trial=trial, stage=stage["name"])
                    rows.extend(batch)
                    durations = sorted(row["http_ms"] for row in batch)
                    summaries.append(
                        {
                            "trial": trial,
                            "stage": stage["name"],
                            "completion_ms": elapsed,
                            "request_p95_ms": durations[
                                math.ceil(len(durations) * 0.95) - 1
                            ],
                            "errors": sum(
                                row["status"] != 200 or "error" in row for row in batch
                            ),
                        }
                    )
        comparisons = []
        for reference in workload.get("references", []):
            expected = Path(reference["file"]).read_bytes()
            if hashlib.sha256(expected).hexdigest() != reference["sha256"]:
                raise ValueError("reference PNG checksum mismatch")
            row = fetch(origin, reference["path"], images)
            row.update(trial=None, stage="reference")
            rows.append(row)
            expected_image = Image.open(io.BytesIO(expected)).convert("RGBA")
            comparisons.append(
                {
                    "path": reference["path"],
                    "passed": row.get("dimensions") == list(expected_image.size)
                    and row.get("rgba_sha256")
                    == hashlib.sha256(expected_image.tobytes()).hexdigest(),
                    "reference_sha256": reference["sha256"],
                }
            )
        report["image_comparisons"] = comparisons
        report["image_acceptance"] = (
            "independent references compared"
            if comparisons
            else "decode only; independent image acceptance not checked"
        )
        if viewer_binary:
            report["browser"] = {"attempted": True, "passed": False}
            report["browser"] = browser_check(viewer_binary, origin, out)
        report["passed"] = (
            all(row["status"] == 200 and "error" not in row for row in rows)
            and all(row["passed"] for row in comparisons)
            and (not viewer_binary or report["browser"]["passed"])
        )
    except Exception as exc:  # noqa: BLE001 -- retain failed qualification evidence
        report["error"] = str(exc)
    finally:
        report["stages"] = summaries
        (out / "requests.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    endpoint = parser.add_mutually_exclusive_group(required=True)
    endpoint.add_argument("--session", type=Path)
    endpoint.add_argument("--url")
    parser.add_argument("--workload", type=Path)
    parser.add_argument("--viewer-binary", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    session = json.loads(args.session.read_text()) if args.session else None
    origin = (session["ApiUrl"] if session else args.url).rstrip("/")
    url = urlsplit(origin)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        parser.error(
            "endpoint must be an HTTP(S) URL without credentials, query or fragment"
        )
    if not 1 <= args.concurrency <= 32 or not 1 <= args.repeats <= 100:
        parser.error("concurrency must be 1–32 and repeats must be 1–100")
    workload = (
        json.loads(args.workload.read_text()) if args.workload else sample_workload()
    )
    for stage in workload["stages"]:
        if not stage["paths"] or any(
            not path.startswith("/") or path.startswith("//") for path in stage["paths"]
        ):
            parser.error("each stage needs nonempty API paths beginning with one slash")
    if not workload["stages"]:
        parser.error("workload must contain at least one stage")
    if args.workload:
        for reference in workload.get("references", []):
            reference["file"] = str(
                (args.workload.resolve().parent / reference["file"]).resolve()
            )
    args.out.mkdir(parents=True, exist_ok=False)
    report = run(
        origin,
        workload,
        args.out,
        args.concurrency,
        args.repeats,
        args.viewer_binary.resolve() if args.viewer_binary else None,
    )
    if session:
        report["deployment"] = session
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"passed": report["passed"], "report": str(args.out / "report.json")},
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
