"""Run P01 HTTP/storage/rendering differentials against the frozen capture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

from common import ROOT, read_json, write_json

API_CASES = ["keys", "datasets", "paging", "filter", "metadata", "columns"]
IMAGE_CASES = [
    "lagoon-red-preview", "lagoon-red-tile-0", "lagoon-red-tile-1",
    "dolomites-preview", "dolomites-tile-0", "dolomites-tile-1",
    "landcover-preview", "landcover-tile-0", "landcover-tile-1",
    "lagoon-mask-preview", "lagoon-mask-tile-0", "lagoon-mask-tile-1",
    "nonsquare", "out-of-bounds", "percentile-p98",
]
ERROR_CASES = ["missing-dataset", "invalid-json", "invalid-keys", "percentile-p99", "method-error"]
CASES = API_CASES + IMAGE_CASES + ERROR_CASES


def request(base_url, record):
    body = None if record["body"] is None else json.dumps(record["body"]).encode()
    req = urllib.request.Request(base_url + record["url"], data=body, method=record["method"], headers={"Origin": "http://localhost:8080", "Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as error:
        response = error
    data = response.read()
    return response.status, {key.lower(): value for key, value in response.headers.items()}, data


def wait_ready(base_url, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with {process.returncode}")
        try:
            urllib.request.urlopen(base_url + "/keys", timeout=.2).read()
            return
        except Exception:
            time.sleep(.05)
    raise TimeoutError("server did not become ready")


def compare_json(expected, actual):
    # JSON object member order is not semantic; list order, types, and values are.
    return expected == actual


def build_sheet(out, reference):
    selected = ["lagoon-red-preview", "lagoon-red-tile-0", "dolomites-preview", "landcover-preview", "lagoon-mask-preview", "out-of-bounds"]
    sheet = Image.new("RGB", (512, len(selected) * 280), "white")
    draw = ImageDraw.Draw(sheet)
    for row, case in enumerate(selected):
        for column, root in enumerate((reference / "images", out / "images")):
            with Image.open(root / f"{case}.png") as image:
                sheet.paste(image.convert("RGBA"), (column * 256, row * 280), image.convert("RGBA"))
        draw.text((4, row * 280 + 258), f"{case}: Python reference | Rust P01", fill="black")
    sheet.save(out / "side-by-side.png")


def run(binary, database, out, port):
    if out.exists():
        raise FileExistsError("choose a fresh P01 validation output directory")
    out.mkdir(parents=True)
    (out / "images").mkdir()
    reference = ROOT / "validation/reference/capture"
    capture = read_json(reference / "capture.json")
    records = {record["id"]: record for record in capture["contract"]["requests"]}
    base_url = f"http://127.0.0.1:{port}"
    environment = dict(os.environ, RUST_BACKTRACE="1")
    with (out / "server.log").open("w") as log:
        process = subprocess.Popen([str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{port}"], cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_ready(base_url, process)
            results = []
            for case in CASES:
                expected = records[case]
                status, headers, body = request(base_url, expected)
                errors = []
                if status != expected["status"]:
                    errors.append(f"status {status} != {expected['status']}")
                for header, value in expected["headers"].items():
                    if headers.get(header) != value:
                        errors.append(f"header {header}: {headers.get(header)!r} != {value!r}")
                if "json" in expected:
                    try:
                        actual = json.loads(body)
                    except Exception as error:
                        errors.append(f"invalid JSON: {error}")
                    else:
                        if not compare_json(expected["json"], actual):
                            errors.append("JSON payload differs")
                elif "text" in expected:
                    if body.decode() != expected["text"]:
                        errors.append("text payload differs")
                elif case in IMAGE_CASES:
                    candidate_path = out / "images" / f"{case}.png"
                    candidate_path.write_bytes(body)
                    with Image.open(candidate_path) as candidate, Image.open(reference / "images" / f"{case}.png") as golden:
                        candidate_array = np.asarray(candidate.convert("RGBA"))
                        golden_array = np.asarray(golden.convert("RGBA"))
                    if candidate_array.shape != golden_array.shape:
                        errors.append(f"decoded shape {candidate_array.shape} != {golden_array.shape}")
                    elif not np.array_equal(candidate_array, golden_array):
                        errors.append(f"{int(np.count_nonzero(candidate_array != golden_array))} decoded RGBA channels differ")
                results.append({"id": case, "passed": not errors, "errors": errors, "status": status, "body_sha256": hashlib.sha256(body).hexdigest()})
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
    build_sheet(out, reference)
    report = {
        "passed": all(result["passed"] for result in results),
        "reference_commit": capture["reference_commit"],
        "database": str(database),
        "binary": str(binary),
        "cases": results,
        "summary": {"passed": sum(item["passed"] for item in results), "failed": sum(not item["passed"] for item in results), "total": len(results)},
        "comparison": "Exact status/selected headers/JSON values and exact decoded RGBA. PNG stream bytes are not compared.",
        "visual_check": "side-by-side.png contains six reference/Rust pairs at native 256-pixel display scale.",
    }
    write_json(out / "report.json", report)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/terracotta")
    parser.add_argument("--database", type=Path, default=ROOT / "validation/artifacts/capture-reproduction/reference.sqlite")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5091)
    args = parser.parse_args()
    run(args.binary.resolve(), args.database.resolve(), args.out.resolve(), args.port)
