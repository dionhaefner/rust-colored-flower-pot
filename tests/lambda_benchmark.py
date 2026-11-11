"""Compare deployed raster functions with paired order, separate initialization and image checks."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import math
import platform
import re
import statistics
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def aws(region, *arguments):
    output = subprocess.check_output(
        ["aws", "--region", region, "--no-cli-pager", "--output", "json", *arguments],
        text=True,
    )
    return json.loads(output) if output.strip() else {}


def stages(mode):
    def view(zoom, offset=0):
        x = int((12.35 + 180) / 360 * 2**zoom)
        y = int((1 - math.asinh(math.tan(math.radians(45.46))) / math.pi) / 2 * 2**zoom)
        suffix = (
            "stretch_range=%5B0%2C3000%5D"
            if mode == "singleband"
            else "r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D"
        )
        prefix = "/singleband/sentinel/red" if mode == "singleband" else "/rgb/sentinel"
        return [
            f"{prefix}/{zoom}/{x + offset + dx}/{y + dy}.png?{suffix}"
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
        ]

    return [
        ("viewport", view(11)),
        ("pan", view(11, 1)),
        ("zoom", view(13)),
        ("detail", view(14)),
        ("revisit", view(11)),
    ]


def request(origin, path):
    start = time.perf_counter()
    row = {"path": path}
    body = b""
    try:
        with urlopen(origin.rstrip("/") + path, timeout=60) as response:
            body = response.read()
            row.update(
                status=response.status,
                bytes=len(body),
                request_id=response.headers.get("x-amzn-requestid"),
            )
    except (HTTPError, URLError, OSError) as exc:
        row.update(status=getattr(exc, "code", 0), error=str(exc))
    row["received_at"] = time.perf_counter()
    row["http_ms"] = (row["received_at"] - start) * 1000
    return row, body


def batch(origin, paths, concurrency, metadata, out, requests):
    with concurrent.futures.ThreadPoolExecutor(concurrency) as pool:
        started = time.perf_counter()
        replies = list(pool.map(lambda path: request(origin, path), paths))
    completion = (max(row["received_at"] for row, _ in replies) - started) * 1000
    decode_start = time.perf_counter()
    for row, body in replies:
        row.pop("received_at")
        row.update(metadata)
        row["concurrency"] = concurrency
        if row["status"] == 200:
            try:
                image = Image.open(io.BytesIO(body)).convert("RGBA")
                row.update(
                    dimensions=list(image.size),
                    rgba_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
                    valid_pixels=sum(pixel[3] != 0 for pixel in image.getdata()),
                )
                if image.size != (256, 256):
                    row["error"] = "unexpected tile dimensions"
                filename = hashlib.sha256(row["path"].encode()).hexdigest() + ".png"
                (out / "images" / metadata["engine"] / filename).write_bytes(body)
            except (OSError, ValueError) as exc:
                row["error"] = str(exc)
        requests.append(row)
    return {
        **metadata,
        "concurrency": concurrency,
        "completion_ms": completion,
        "client_decode_record_ms": (time.perf_counter() - decode_start) * 1000,
        "errors": sum(row["status"] != 200 or "error" in row for row, _ in replies),
        "requests": len(paths),
    }


def reset(region, function):
    variables = function["configuration"]["Environment"]["Variables"].copy()
    variables["BENCHMARK_GENERATION"] = uuid.uuid4().hex
    aws(
        region,
        "lambda",
        "update-function-configuration",
        "--function-name",
        function["FunctionName"],
        "--environment",
        json.dumps({"Variables": variables}),
    )
    aws(
        region,
        "lambda",
        "wait",
        "function-updated-v2",
        "--function-name",
        function["FunctionName"],
    )


def collect_reports(region, functions, start_ms, out):
    records = {}
    for engine, function in functions.items():
        result = aws(
            region,
            "logs",
            "filter-log-events",
            "--log-group-name",
            "/aws/lambda/" + function["FunctionName"],
            "--start-time",
            str(start_ms),
            "--filter-pattern",
            '"REPORT RequestId:"',
        )
        (out / (engine + "-lambda-reports.json")).write_text(
            json.dumps(result, indent=2) + "\n"
        )
        for event in result.get("events", []):
            message = event["message"]
            match = re.search(r"REPORT RequestId: (\S+)", message)
            if not match:
                continue
            record = {"engine": engine, "log_stream": event["logStreamName"]}
            for name, pattern in {
                "duration_ms": r"\bDuration: ([\d.]+) ms",
                "billed_ms": r"Billed Duration: ([\d.]+) ms",
                "init_ms": r"Init Duration: ([\d.]+) ms",
                "max_memory_mib": r"Max Memory Used: ([\d.]+) MB",
            }.items():
                value = re.search(pattern, message)
                if value:
                    record[name] = float(value.group(1))
            records[match.group(1)] = record
    return records


def summarize(rows):
    groups = {}
    for row in rows:
        if row["phase"] != "measured":
            continue
        key = (row["mode"], row["concurrency"], row["stage"])
        groups.setdefault(key, {}).setdefault(row["trial"], {})[row["engine"]] = row
    results = []
    for (mode, concurrency, stage), trials in groups.items():
        valid = [
            arms
            for arms in trials.values()
            if set(arms) == {"native", "terracotta"}
            and all(row["errors"] == 0 for row in arms.values())
        ]
        paired = [
            arms["terracotta"]["completion_ms"] / arms["native"]["completion_ms"]
            for arms in valid
        ]
        results.append(
            {
                "mode": mode,
                "concurrency": concurrency,
                "stage": stage,
                "paired_trials": len(paired),
                "excluded_pairs": len(trials) - len(valid),
                "native_median_ms": statistics.median(
                    arms["native"]["completion_ms"] for arms in valid
                )
                if valid
                else None,
                "terracotta_median_ms": statistics.median(
                    arms["terracotta"]["completion_ms"] for arms in valid
                )
                if valid
                else None,
                "median_paired_speedup": statistics.median(paired) if paired else None,
                "paired_speedups": paired,
                "errors": sum(
                    row["errors"] for arms in trials.values() for row in arms.values()
                ),
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.trials < 3:
        parser.error("use at least three paired trials")
    args.out.mkdir(parents=True, exist_ok=False)
    deployment = json.loads(args.deployment.read_text())
    functions = deployment["functions"]
    if set(functions) != {"native", "terracotta"}:
        raise ValueError("both deployed backends are required")
    for engine in functions:
        (args.out / "images" / engine).mkdir(parents=True)
    rows, requests = [], []
    report = {
        "passed": False,
        "deployment": deployment,
        "client_platform": platform.platform(),
        "trials": args.trials,
        "scope": "full-size checksum-pinned Sentinel-2 COGs in private same-region S3",
        "conditions": {
            "viewport_tiles": 9,
            "concurrency": [1, 4],
            "fresh_state": "environment update before each paired trial; first request is an image",
            "warm_state": "each navigation sequence runs once as untimed warmup before measurement",
            "transport": "Function URLs, fresh HTTP connections; response receipt excludes client image decode/record",
            "order": "backend order rotates by trial and case",
            "finished_image_cache": 0,
        },
        "rows": rows,
    }
    start_ms = int(time.time() * 1000)
    try:
        golden_bytes = (ROOT / "tests/fixtures/demo/preview.png").read_bytes()
        golden_manifest = json.loads(
            (ROOT / "tests/fixtures/demo/manifest.json").read_text()
        )
        if (
            hashlib.sha256(golden_bytes).hexdigest()
            != golden_manifest["preview_sha256"]
        ):
            raise ValueError("independent preview checksum mismatch")
        golden = Image.open(io.BytesIO(golden_bytes)).convert("RGBA")
        report["preview_qualifications"] = []
        for engine, function in functions.items():
            row, body = request(
                function["ApiUrl"],
                "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D",
            )
            if row["status"] != 200:
                raise RuntimeError(f"{engine} live preview failed: {row}")
            image = Image.open(io.BytesIO(body)).convert("RGBA")
            if image.size != golden.size or image.tobytes() != golden.tobytes():
                raise ValueError(
                    f"{engine} live preview differs from the independent reference"
                )
            report["preview_qualifications"].append(
                {"engine": engine, "passed": True, "request_id": row.get("request_id")}
            )
        for trial in range(args.trials):
            order = (
                ["native", "terracotta"] if trial % 2 == 0 else ["terracotta", "native"]
            )
            for engine in order:
                reset(deployment["region"], functions[engine])
            for engine in order:
                metadata = {
                    "engine": engine,
                    "trial": trial,
                    "mode": "singleband",
                    "stage": "viewport",
                    "phase": "fresh",
                }
                result = batch(
                    functions[engine]["ApiUrl"],
                    stages("singleband")[0][1],
                    4,
                    metadata,
                    args.out,
                    requests,
                )
                rows.append(result)
                print(json.dumps(result), flush=True)
            for case, (mode, concurrency) in enumerate(
                (mode, concurrency)
                for mode in ("singleband", "rgb")
                for concurrency in (1, 4)
            ):
                case_order = order if case % 2 == 0 else order[::-1]
                for engine in case_order:
                    for phase in ("warmup", "measured"):
                        for stage, paths in stages(mode):
                            metadata = {
                                "engine": engine,
                                "trial": trial,
                                "mode": mode,
                                "stage": stage,
                                "phase": phase,
                            }
                            result = batch(
                                functions[engine]["ApiUrl"],
                                paths,
                                concurrency,
                                metadata,
                                args.out,
                                requests,
                            )
                            rows.append(result)
                            if phase == "measured":
                                print(json.dumps(result), flush=True)
            (args.out / "progress.json").write_text(
                json.dumps(
                    {
                        "completed_trials": trial + 1,
                        "request_errors": sum(
                            row["status"] != 200 or "error" in row for row in requests
                        ),
                    }
                )
                + "\n"
            )
        # Management logs can arrive after the corresponding HTTP response.
        expected_ids = {row["request_id"] for row in requests if row.get("request_id")}
        logs = {}
        for _ in range(6):
            logs = collect_reports(deployment["region"], functions, start_ms, args.out)
            if expected_ids <= logs.keys():
                break
            time.sleep(5)
        for row in requests:
            row["lambda_report"] = logs.get(row.get("request_id"))
        report.update(
            summary=summarize(rows),
            request_count=len(requests),
            request_errors=sum(
                row["status"] != 200 or "error" in row for row in requests
            ),
            matched_lambda_reports=sum(
                row.get("lambda_report") is not None for row in requests
            ),
            measured_initializations=sum(
                "init_ms" in (row.get("lambda_report") or {})
                for row in requests
                if row["phase"] == "measured"
            ),
        )
        report["passed"] = (
            report["request_errors"] == 0
            and report["matched_lambda_reports"] == len(requests)
            and report["measured_initializations"] == 0
        )
    except BaseException as exc:
        report["passed"] = False
        report["run_error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["request_count"] = len(requests)
        report["request_errors"] = sum(
            row["status"] != 200 or "error" in row for row in requests
        )
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
            except (subprocess.CalledProcessError, ValueError) as exc:
                restoration_errors.append({"engine": engine, "error": str(exc)})
        report["configuration_restoration_errors"] = restoration_errors
        if restoration_errors:
            report["passed"] = False
        (args.out / "requests.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in requests)
        )
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "passed",
                    "request_count",
                    "request_errors",
                    "matched_lambda_reports",
                    "measured_initializations",
                )
            },
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
