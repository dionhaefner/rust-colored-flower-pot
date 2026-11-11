"""Measure complete Leaflet view, pan and zoom actions against deployed Lambda backends."""

from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import shutil
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from lambda_benchmark import aws, collect_reports, reset
from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = (
    "first_view",
    "pan",
    "zoom_in",
    "detail",
    "detail_pan",
    "zoom_out",
    "revisit",
)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def peak(intervals):
    events = [(start, 1) for start, end in intervals if end >= start]
    events += [(end, -1) for start, end in intervals if end >= start]
    active = maximum = 0
    for _, change in sorted(events):
        active += change
        maximum = max(maximum, active)
    return maximum


def map_case(
    browser,
    origin,
    engine,
    mode,
    trial,
    local,
    out,
    records,
    *,
    ignore_https_errors=False,
):
    suffix = (
        "stretch_range=%5B0%2C3000%5D"
        if mode == "singleband"
        else "r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D"
    )
    prefix = "/singleband/sentinel/red" if mode == "singleband" else "/rgb/sentinel"
    tile_url = origin.rstrip("/") + prefix + "/{z}/{x}/{y}.png?" + suffix
    context = browser.new_context(
        viewport={"width": 1600, "height": 1000},
        device_scale_factor=1,
        ignore_https_errors=ignore_https_errors,
    )
    page = context.new_page()
    session = context.new_cdp_session(page)
    session.send("Network.enable")
    network = {}
    completed = {}

    def response_received(event):
        network[event["response"]["url"]] = {
            **event["response"],
            "cdp_request_id": event["requestId"],
        }

    session.on("Network.responseReceived", response_received)
    session.on(
        "Network.loadingFinished",
        lambda event: completed.update({event["requestId"]: event["timestamp"]}),
    )
    errors, pending, finished = [], [], []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on(
        "requestfinished",
        lambda request: (
            finished.append(request)
            if request.resource_type == "image"
            and urlsplit(request.url).netloc == urlsplit(origin).netloc
            else None
        ),
    )
    page.on(
        "requestfailed",
        lambda request: (
            pending.append(request)
            if request.resource_type == "image"
            and urlsplit(request.url).netloc == urlsplit(origin).netloc
            else None
        ),
    )
    rows = []
    try:
        page.goto(local, wait_until="load")
        for action in ACTIONS:
            finished.clear()
            pending.clear()
            network.clear()
            completed.clear()
            metadata = {
                "trial": trial,
                "engine": engine,
                "mode": mode,
                "stage": action,
                "phase": "measured",
            }
            try:
                result = page.evaluate(
                    "([action, url]) => window.navigate(action, url)",
                    [action, tile_url],
                )
            except PlaywrightError as exc:
                result = {"error": str(exc), "completion_ms": None}
            intervals = []
            outstanding = []
            paths = []
            failures = 0
            for request in [*finished, *pending]:
                response = request.response()
                timing = request.timing
                path = urlsplit(request.url).path + "?" + urlsplit(request.url).query
                row = {
                    **metadata,
                    "path": path,
                    "status": response.status if response else 0,
                    "timing": timing,
                    "browser_cache": bool(
                        network.get(request.url, {}).get("fromDiskCache", False)
                    ),
                    "protocol": network.get(request.url, {}).get("protocol"),
                }
                wire = network.get(request.url, {})
                wire_timing = wire.get("timing", {})
                wire_end = completed.get(wire.get("cdp_request_id"))
                row["connection_id"] = wire.get("connectionId")
                if (
                    not row["browser_cache"]
                    and wire_timing.get("sendStart", -1) >= 0
                    and wire_end is not None
                ):
                    intervals.append(
                        (
                            wire_timing["requestTime"] * 1000
                            + wire_timing["sendStart"],
                            wire_end * 1000,
                        )
                    )
                if (
                    not row["browser_cache"]
                    and timing["requestStart"] >= 0
                    and timing["responseEnd"] >= 0
                ):
                    outstanding.append(
                        (
                            timing["startTime"],
                            timing["startTime"] + timing["responseEnd"],
                        )
                    )
                if response:
                    row["request_id"] = response.headers.get(
                        "x-amzn-requestid"
                    ) or response.headers.get("x-benchmark-request-id")
                    row["server_address"] = response.server_addr()
                if response and response.status == 200:
                    body = response.body()
                    image = Image.open(io.BytesIO(body)).convert("RGBA")
                    row.update(
                        bytes=len(body),
                        dimensions=list(image.size),
                        rgba_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
                    )
                    filename = hashlib.sha256(path.encode()).hexdigest() + ".png"
                    (out / "images" / engine / filename).write_bytes(body)
                    if image.size != (256, 256):
                        row["error"] = "unexpected tile dimensions"
                else:
                    row["error"] = request.failure or f"HTTP {row['status']}"
                failures += row["status"] != 200 or "error" in row
                records.append(row)
                paths.append(path)
            row = {
                **metadata,
                **result,
                "tile_requests": len(paths),
                "request_paths": paths,
                "maximum_network_inflight": peak(intervals),
                "maximum_browser_outstanding": peak(outstanding),
                "errors": failures,
                "javascript_errors": list(errors),
            }
            rows.append(row)
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in row.items()
                        if k not in ("visible", "request_paths")
                    }
                ),
                flush=True,
            )
            page.screenshot(path=str(out / f"{engine}-{mode}-{trial}-{action}.png"))
            if "error" in result or failures or errors:
                break
        return rows
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    deployment = json.loads(args.deployment.read_text())
    functions = deployment["functions"]
    web = args.out / "web"
    web.mkdir()
    shutil.copy2(ROOT / "tests/benchmarks/lambda-map.html", web / "index.html")
    for name in ("leaflet.js", "leaflet.css"):
        shutil.copy2(ROOT / "src/assets/client/vendor" / name, web / name)
    handler = functools.partial(QuietHandler, directory=str(web.resolve()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = f"http://127.0.0.1:{server.server_port}/"
    rows, records = [], []
    original_concurrency = {}
    report = {
        "passed": False,
        "deployment": deployment,
        "rows": rows,
        "viewport": [1600, 1000],
        "actions": ACTIONS,
        "requested_concurrency": 64,
        "trials": args.trials,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "map_fixture_sha256": hashlib.sha256(
            (web / "index.html").read_bytes()
        ).hexdigest(),
        "leaflet_sha256": hashlib.sha256((web / "leaflet.js").read_bytes()).hexdigest(),
        "scope": "real Leaflet map; first image viewport then animated pan, zoom and revisit; browser cache and keepBuffer retained within each map session",
        "timing": "performance.now at action until Leaflet current tiles loaded, tile fades and map animation finished, visible images decoded and two requestAnimationFrame callbacks; no claim of compositor presentation timestamp",
    }
    start_ms = int(time.time() * 1000)
    for engine in functions:
        (args.out / "images" / engine).mkdir(parents=True)
    try:
        for engine, function in functions.items():
            original_concurrency[engine] = aws(
                deployment["region"],
                "lambda",
                "get-function-concurrency",
                "--function-name",
                function["FunctionName"],
            )
            aws(
                deployment["region"],
                "lambda",
                "put-function-concurrency",
                "--function-name",
                function["FunctionName"],
                "--reserved-concurrent-executions",
                "64",
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            report["browser_version"] = browser.version
            try:
                for trial in range(args.trials):
                    order = (
                        ["native", "terracotta"]
                        if trial % 2 == 0
                        else ["terracotta", "native"]
                    )
                    for mode in ("singleband", "rgb"):
                        for engine in order if mode == "singleband" else order[::-1]:
                            reset(deployment["region"], functions[engine])
                            rows.extend(
                                map_case(
                                    browser,
                                    functions[engine]["ApiUrl"],
                                    engine,
                                    mode,
                                    trial,
                                    local,
                                    args.out,
                                    records,
                                )
                            )
                            (args.out / "progress.json").write_text(
                                json.dumps(
                                    {
                                        "completed_maps": len(rows) // len(ACTIONS),
                                        "errors": sum(r["errors"] for r in rows),
                                    }
                                )
                                + "\n"
                            )
            finally:
                browser.close()
        network_records = [r for r in records if not r["browser_cache"]]
        expected_ids = {r["request_id"] for r in network_records if r.get("request_id")}
        logs = {}
        for _ in range(6):
            logs = collect_reports(deployment["region"], functions, start_ms, args.out)
            if expected_ids <= logs.keys():
                break
            time.sleep(5)
        for record in records:
            record["lambda_report"] = logs.get(record.get("request_id"))
        report["matched_lambda_reports"] = sum(
            bool(r["lambda_report"]) for r in network_records
        )
        report["network_request_count"] = len(network_records)
        report["browser_cache_hits"] = len(records) - len(network_records)
        report["passed"] = (
            len(rows) == args.trials * 2 * 2 * len(ACTIONS)
            and all(
                not r.get("error") and r["errors"] == 0 and not r["javascript_errors"]
                for r in rows
            )
            and len(expected_ids) == len(network_records)
            and expected_ids <= logs.keys()
        )
    except BaseException as exc:
        report["run_error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        restoration_errors = []
        for engine, function in functions.items():
            try:
                aws(
                    deployment["region"],
                    "lambda",
                    "update-function-configuration",
                    "--function-name",
                    function["FunctionName"],
                    "--environment",
                    json.dumps(
                        {
                            "Variables": function["configuration"]["Environment"][
                                "Variables"
                            ]
                        }
                    ),
                )
                aws(
                    deployment["region"],
                    "lambda",
                    "wait",
                    "function-updated-v2",
                    "--function-name",
                    function["FunctionName"],
                )
                if engine in original_concurrency:
                    value = original_concurrency[engine].get(
                        "ReservedConcurrentExecutions"
                    )
                    if value is None:
                        aws(
                            deployment["region"],
                            "lambda",
                            "delete-function-concurrency",
                            "--function-name",
                            function["FunctionName"],
                        )
                    else:
                        aws(
                            deployment["region"],
                            "lambda",
                            "put-function-concurrency",
                            "--function-name",
                            function["FunctionName"],
                            "--reserved-concurrent-executions",
                            str(value),
                        )
            except (subprocess.CalledProcessError, ValueError) as exc:
                restoration_errors.append({"engine": engine, "error": str(exc)})
        server.shutdown()
        server.server_close()
        report.update(
            configuration_restoration_errors=restoration_errors,
            request_count=len(records),
            request_errors=sum(r["status"] != 200 or "error" in r for r in records),
        )
        if restoration_errors:
            report["passed"] = False
        (args.out / "requests.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records)
        )
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "passed",
                    "request_count",
                    "request_errors",
                    "matched_lambda_reports",
                )
            },
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
