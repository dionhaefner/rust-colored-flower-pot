"""Exercise repeated native raster requests and bounded server resources."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RASTER = ROOT / "tests/fixtures/corpus/red.tif"
MANIFEST = ROOT / "tests/fixtures/corpus/manifest.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def operation(binary: Path, out: Path, request: dict) -> None:
    instruction = out / "operation.json"
    instruction.write_text(json.dumps(request))
    result = subprocess.run(
        [str(binary), "data-operation", str(instruction)],
        capture_output=True,
        text=True,
        check=True,
    )
    response = json.loads(result.stdout)
    if isinstance(response, dict) and "error" in response:
        raise AssertionError(response["error"])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def ready(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if fetch(url)[0] == 200:
                return
        except (urllib.error.URLError, TimeoutError):
            if process.poll() is not None:
                raise RuntimeError("server exited while starting") from None
            time.sleep(0.1)
    raise RuntimeError("server did not start")


def resources(pid: int) -> tuple[int, int]:
    status = Path(f"/proc/{pid}/status").read_text()
    rss_kib = int(
        next(
            line.split()[1] for line in status.splitlines() if line.startswith("VmRSS:")
        )
    )
    descriptors = len(list(Path(f"/proc/{pid}/fd").iterdir()))
    return rss_kib * 1024, descriptors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    require(args.seconds >= 1, "duration must be positive")
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = next(
        row
        for row in json.loads(MANIFEST.read_text())["fixtures"]
        if row["id"] == "red"
    )
    require(
        hashlib.sha256(RASTER.read_bytes()).hexdigest() == fixture["sha256"],
        "raster fixture changed",
    )
    database = out / "catalog.sqlite"
    operation(
        binary,
        out,
        {
            "operation": "create",
            "path": str(database),
            "keys": [{"key": "scene", "description": ""}],
        },
    )
    operation(
        binary,
        out,
        {
            "operation": "insert",
            "path": str(database),
            "keys": ["lagoon"],
            "raster": str(RASTER),
        },
    )
    lon = sum(fixture["geographic_bounds"][::2]) / 2
    lat = sum(fixture["geographic_bounds"][1::2]) / 2
    zoom = 14
    scale = 2**zoom
    x = int((lon + 180) / 360 * scale)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tile = f"{base}/singleband/lagoon/{zoom}/{x}/{y}.png?stretch_range=%5B0%2C3000%5D"
    log_path = out / "service.log"
    log = log_path.open("w")
    service = subprocess.Popen(
        [str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{port}"],
        stdout=log,
        stderr=log,
        env={
            **os.environ,
            "TC_RASTER_WORKERS": "3",
            "TC_RASTER_QUEUE_CAPACITY": "64",
            "TC_RASTER_CACHE_BYTES": "0",
            "TC_RENDER_CACHE_BYTES": "0",
            "TC_EXPOSE_METRICS": "true",
        },
    )
    try:
        ready(f"{base}/keys", service)
        baseline_rss, baseline_fds = resources(service.pid)
        status, image = fetch(tile)
        require(
            status == 200 and image.startswith(b"\x89PNG\r\n\x1a\n"),
            "initial raster tile failed",
        )
        expected_hash = hashlib.sha256(image).hexdigest()
        # Distinct grids exercise several workers; duplicate grids also share active work.
        tiles = [
            tile + f"&tile_size=%5B{size}%2C{size}%5D" for size in (128, 192, 256, 320)
        ]
        expected = []
        for url in tiles:
            status, body = fetch(url)
            require(status == 200, "initial resource-test grid failed")
            expected.append(hashlib.sha256(body).hexdigest())
        bad_status, _ = fetch(f"{base}/singleband/lagoon/preview.png?tile_size=invalid")
        require(bad_status == 400, "invalid tile size did not return 400")
        deadline = time.monotonic() + args.seconds

        def worker(index: int) -> int:
            count = 0
            while time.monotonic() < deadline:
                status, body = fetch(tiles[index % len(tiles)])
                require(status == 200, f"raster request returned HTTP {status}")
                require(
                    hashlib.sha256(body).hexdigest() == expected[index % len(tiles)],
                    "tile changed during repeated reads",
                )
                count += 1
            return count

        peak_rss, peak_fds = baseline_rss, baseline_fds
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, index) for index in range(8)]
            while not all(future.done() for future in futures):
                require(service.poll() is None, "server exited during load")
                observed_rss, observed_fds = resources(service.pid)
                peak_rss = max(peak_rss, observed_rss)
                peak_fds = max(peak_fds, observed_fds)
                time.sleep(0.25)
            counts = [future.result() for future in futures]
        time.sleep(1)
        rss, fds = resources(service.pid)
        peak_rss = max(peak_rss, rss)
        peak_fds = max(peak_fds, fds)
        require(sum(counts) >= 80, "too few successful raster requests")
        require(
            peak_rss < 512 * 1024 * 1024, f"server RSS exceeded 512 MiB: {peak_rss}"
        )
        require(
            peak_fds <= baseline_fds + 40,
            f"file descriptor peak grew: {baseline_fds} to {peak_fds}",
        )
        require(
            fds <= baseline_fds + 16,
            f"file descriptor count grew: {baseline_fds} to {fds}",
        )
        require(fetch(f"{base}/keys")[0] == 200, "server stopped responding after load")
        metrics_status, metrics_body = fetch(f"{base}/__terracotta/metrics")
        require(metrics_status == 200, "enabled metrics route failed")
        metrics = json.loads(metrics_body)
        require(metrics["workers"] == 3, "metrics worker count changed")
        require(metrics["queue_capacity"] == 64, "metrics queue capacity changed")
        require(
            metrics["submitted"] + metrics["render_cache"]["coalesced"] >= sum(counts)
            and metrics["completed"] + metrics["render_cache"]["coalesced"]
            >= sum(counts)
            and metrics["peak_in_flight"] >= 2,
            "metrics did not record concurrent raster work",
        )
        sentinel = "rcfp-test-secret-731c"
        fetch(f"{base}/missing?access_token={sentinel}")
        log.flush()
        require(
            sentinel not in log_path.read_text(),
            "request secret appeared in server log",
        )
        report = {
            "passed": True,
            "duration_seconds": args.seconds,
            "requests": sum(counts),
            "workers": len(counts),
            "tile_sha256": expected_hash,
            "baseline_rss_bytes": baseline_rss,
            "peak_observed_rss_bytes": peak_rss,
            "final_rss_bytes": rss,
            "baseline_fds": baseline_fds,
            "peak_fds": peak_fds,
            "final_fds": fds,
            "metrics": metrics,
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        service.terminate()
        service.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    main()
