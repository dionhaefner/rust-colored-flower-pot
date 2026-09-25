"""Exercise P02 overload, timeout accounting, and post-failure recovery."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

from common import COMMIT, ROOT, write_json
from range_server import RangeServer


def clone_scene(connection, scene, path):
    for table in ("datasets", "metadata"):
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        row = list(
            connection.execute(
                f"SELECT * FROM {table} WHERE scene='lagoon' AND band='red'"
            ).fetchone()
        )
        row[columns.index("scene")] = scene
        if table == "datasets":
            row[columns.index("path")] = path
        placeholders = ",".join("?" for _ in row)
        connection.execute(f"INSERT INTO {table} VALUES ({placeholders})", row)


def prepare_database(target, range_port):
    shutil.copy2(
        ROOT / "validation/artifacts/capture-reproduction/reference.sqlite", target
    )
    with sqlite3.connect(target) as connection:
        clone_scene(
            connection,
            "slow",
            f"http://127.0.0.1:{range_port}/subsets/lagoon-red.tif",
        )
        clone_scene(connection, "broken", str(target.parent / "missing-raster.tif"))


def fetch(base_url, url, timeout=30):
    try:
        response = urllib.request.urlopen(base_url + url, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    body = response.read()
    return response.status, body.decode(errors="replace")


def wait_ready(base_url, process):
    for _ in range(400):
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with {process.returncode}")
        try:
            urllib.request.urlopen(base_url + "/keys", timeout=0.2).read()
            return
        except Exception:
            time.sleep(0.05)
    raise TimeoutError("server did not become ready")


def metrics(base_url):
    status, body = fetch(base_url, "/__terracotta/metrics")
    if status != 200:
        raise RuntimeError(f"metrics returned {status}")
    return json.loads(body)


def run(binary, output, server_port, range_port):
    if output.exists():
        raise FileExistsError("choose a fresh P02 failure output directory")
    output.mkdir(parents=True)
    range_server = RangeServer(ROOT / ".cache/corpus", range_port, delay_seconds=0.02)
    database = output / "failure.sqlite"
    prepare_database(database, range_server.port)
    environment = dict(
        os.environ,
        RUST_BACKTRACE="1",
        TC_RASTER_WORKERS="1",
        TC_RASTER_QUEUE_CAPACITY="1",
        TC_RASTER_TIMEOUT_MS="5",
        TC_RASTER_CACHE_BYTES=str(64 * 1024**2),
    )
    base_url = f"http://127.0.0.1:{server_port}"
    with (output / "server.log").open("w") as log:
        process = subprocess.Popen(
            [str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{server_port}"],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_ready(base_url, process)
            before = metrics(base_url)
            urls = [
                f"/singleband/slow/red/preview.png?tile_size=%5B{256 + index}%2C256%5D"
                for index in range(10)
            ]
            with ThreadPoolExecutor(max_workers=len(urls)) as pool:
                responses = list(pool.map(lambda url: fetch(base_url, url), urls))
            after_responses = metrics(base_url)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                drained = metrics(base_url)
                if (
                    drained["in_flight"] == 0
                    and drained["completed"] + drained["failed"] >= drained["submitted"]
                ):
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("native work did not drain after response timeouts")

            broken_status, broken_body = fetch(
                base_url, "/singleband/broken/red/preview.png"
            )
            health_url = "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D"
            warming_status, _ = fetch(base_url, health_url)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                recovered = metrics(base_url)
                if (
                    recovered["in_flight"] == 0
                    and recovered["completed"] + recovered["failed"]
                    >= recovered["submitted"]
                ):
                    break
                time.sleep(0.05)
            healthy_status, _ = fetch(base_url, health_url)
            final = metrics(base_url)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            range_server.close()

    statuses = [status for status, _ in responses]
    broken_meaningful = broken_status == 504 or (
        broken_status == 400 and "error while reading file" in broken_body
    )
    passed = (
        503 in statuses
        and 504 in statuses
        and after_responses["overloaded"] > before["overloaded"]
        and after_responses["timed_out"] > before["timed_out"]
        and drained["in_flight"] == 0
        and drained["completed"] + drained["failed"] == drained["submitted"]
        and broken_meaningful
        and warming_status in (200, 504)
        and healthy_status == 200
        and final["failed"] > drained["failed"]
        and final["completed"] > drained["completed"]
        and final["open_file_descriptors"]
        <= before["open_file_descriptors"] + final["workers"] * 4
    )
    report = {
        "passed": passed,
        "reference_commit": COMMIT,
        "configuration": {
            "workers": 1,
            "queue_capacity": 1,
            "response_wait_timeout_ms": 5,
            "cache_bytes": 64 * 1024**2,
            "range_response_delay_ms": 20,
        },
        "responses": [{"status": status, "body": body} for status, body in responses],
        "status_counts": {str(status): statuses.count(status) for status in sorted(set(statuses))},
        "metrics_before": before,
        "metrics_after_responses": after_responses,
        "metrics_after_native_drain": drained,
        "recovery": {
            "broken_status": broken_status,
            "warming_status": warming_status,
            "healthy_status": healthy_status,
            "handles_bounded": final["open_file_descriptors"]
            <= before["open_file_descriptors"] + final["workers"] * 4,
            "metrics_final": final,
        },
        "timeout_semantics": "504 bounds response waiting only. The native GDAL job is not cancelled; submitted work remains accounted until completion.",
        "range_server": range_server.snapshot(),
    }
    database.unlink()
    write_json(output / "report.json", report)
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/terracotta")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5095)
    parser.add_argument("--range-port", type=int, default=5195)
    args = parser.parse_args()
    run(args.binary.resolve(), args.out.resolve(), args.port, args.range_port)
