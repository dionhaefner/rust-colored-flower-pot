"""Exercise the full WorldCover parent through local and HTTP range reads."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request

import numpy as np
from PIL import Image
import psutil
import rasterio
from rasterio.warp import transform_bounds

from common import COMMIT, ROOT, CACHE, read_json, sha256, write_json
from range_server import RangeServer


def prepare_database(source, target, range_port):
    shutil.copy2(
        ROOT / "validation/artifacts/capture-reproduction/reference.sqlite", target
    )
    with rasterio.open(source) as dataset:
        bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds)
        source_info = {
            "width": dataset.width,
            "height": dataset.height,
            "dtype": dataset.dtypes[0],
            "overviews": dataset.overviews(1),
            "bounds_wgs84": list(bounds),
            "block_shapes": [list(shape) for shape in dataset.block_shapes],
        }
    with sqlite3.connect(target) as connection:
        connection.execute(
            "UPDATE datasets SET path=? WHERE scene='lagoon' AND band='classes'",
            (str(source),),
        )
        connection.execute(
            "UPDATE metadata SET bounds_north=?, bounds_east=?, bounds_south=?, bounds_west=? WHERE scene='lagoon' AND band='classes'",
            bounds,
        )
        connection.execute(
            "INSERT INTO datasets SELECT 'remote', band, ? FROM datasets WHERE scene='lagoon' AND band='classes'",
            (f"http://127.0.0.1:{range_port}/worldcover.tif",),
        )
        connection.execute(
            "INSERT INTO metadata SELECT 'remote', band, bounds_north, bounds_east, bounds_south, bounds_west, convex_hull, valid_percentage, min, max, mean, stdev, percentiles, metadata FROM metadata WHERE scene='lagoon' AND band='classes'"
        )
    return source_info


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


def fetch(base_url, url):
    started = time.perf_counter()
    data = urllib.request.urlopen(base_url + url, timeout=120).read()
    return data, time.perf_counter() - started


def rgba(data):
    import io

    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGBA")).copy()


def metrics(base_url):
    return json.loads(
        urllib.request.urlopen(base_url + "/__terracotta/metrics", timeout=10).read()
    )


def run(binary, output, server_port, range_port, budget_bytes):
    if output.exists():
        raise FileExistsError("choose a fresh P02 stress output directory")
    output.mkdir(parents=True)
    source = (CACHE / "worldcover.tif").resolve()
    expected_hash = read_json(ROOT / "validation/reference/assets.json")["assets"][-1][
        "sha256"
    ]
    actual_hash = sha256(source)
    if actual_hash != expected_hash:
        raise RuntimeError("full WorldCover source checksum changed")
    range_server = RangeServer(CACHE, range_port)
    database = output / "stress.sqlite"
    source_info = prepare_database(source, database, range_server.port)
    views = read_json(ROOT / "validation/reference/capture/views.json")
    palette_query = next(
        view["url"].split("?", 1)[1]
        for view in views
        if view["id"] == "landcover-preview"
    )
    python_stress = read_json(ROOT / "validation/artifacts/stress/stress.json")
    x, y, z = python_stress["detail_xyz"]
    case_paths = {
        "preview": f"preview.png?{palette_query}",
        "detail": f"{z}/{x}/{y}.png?{palette_query}",
    }
    base_url = f"http://127.0.0.1:{server_port}"
    environment = dict(
        os.environ,
        RUST_BACKTRACE="1",
        TC_RASTER_WORKERS="3",
        TC_RASTER_QUEUE_CAPACITY="8",
        TC_RASTER_TIMEOUT_MS="120000",
        TC_RASTER_CACHE_BYTES=str(64 * 1024**2),
    )
    with (output / "server.log").open("w") as log:
        process = subprocess.Popen(
            [str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{server_port}"],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        measured = psutil.Process(process.pid)
        peak = [measured.memory_info().rss]
        finished = threading.Event()

        def sample():
            while not finished.wait(0.005):
                try:
                    peak[0] = max(peak[0], measured.memory_info().rss)
                except psutil.NoSuchProcess:
                    return

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            wait_ready(base_url, process)
            initial_metrics = metrics(base_url)
            local = {}
            for name, suffix in case_paths.items():
                data, seconds = fetch(base_url, f"/singleband/lagoon/classes/{suffix}")
                (output / f"local-{name}.png").write_bytes(data)
                expected = rgba(
                    (ROOT / "validation/artifacts/stress" / f"{name}.png").read_bytes()
                )
                local[name] = {
                    "seconds": seconds,
                    "exact_python": bool(np.array_equal(rgba(data), expected)),
                    "bytes": len(data),
                }

            traffic_before = range_server.snapshot()
            remote = {}
            remote_data = {}
            for name, suffix in case_paths.items():
                data, seconds = fetch(base_url, f"/singleband/remote/classes/{suffix}")
                remote_data[name] = data
                (output / f"remote-{name}.png").write_bytes(data)
                remote[name] = {
                    "cold_seconds": seconds,
                    "exact_local": bool(
                        np.array_equal(
                            rgba(data), (rgba((output / f"local-{name}.png").read_bytes()))
                        )
                    ),
                    "bytes": len(data),
                }
            traffic_cold = range_server.snapshot()
            for name, suffix in case_paths.items():
                data, seconds = fetch(base_url, f"/singleband/remote/classes/{suffix}")
                remote[name]["warm_seconds"] = seconds
                remote[name]["warm_exact"] = bool(
                    np.array_equal(rgba(data), rgba(remote_data[name]))
                )
            traffic_warm = range_server.snapshot()
            final_metrics = metrics(base_url)
            peak[0] = max(peak[0], measured.memory_info().rss)
        finally:
            finished.set()
            sampler.join()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            range_server.close()

    passed = (
        all(item["exact_python"] for item in local.values())
        and all(item["exact_local"] and item["warm_exact"] for item in remote.values())
        and traffic_cold["range_requests"] > traffic_before["range_requests"]
        and traffic_cold["bytes_sent"] > traffic_before["bytes_sent"]
        and traffic_warm == traffic_cold
        and final_metrics["peak_in_flight"] <= final_metrics["workers"]
        and final_metrics["open_file_descriptors"]
        <= initial_metrics["open_file_descriptors"] + final_metrics["workers"] * 4
        and peak[0] <= budget_bytes
    )
    report = {
        "passed": passed,
        "reference_commit": COMMIT,
        "budget_bytes": budget_bytes,
        "sampled_peak_rss_bytes": peak[0],
        "handles_bounded": final_metrics["open_file_descriptors"]
        <= initial_metrics["open_file_descriptors"] + final_metrics["workers"] * 4,
        "source": {
            **source_info,
            "path": str(source),
            "file_bytes": source.stat().st_size,
            "sha256": actual_hash,
            "tiff_variant": "classic TIFF / publisher COG",
            "decoded_value_bytes": source_info["width"] * source_info["height"],
        },
        "local": local,
        "remote": remote,
        "traffic": {
            "before": traffic_before,
            "after_cold": traffic_cold,
            "after_warm": traffic_warm,
            "warm_added_no_traffic": traffic_warm == traffic_cold,
        },
        "metrics_initial": initial_metrics,
        "metrics_final": final_metrics,
        "detail_xyz": [x, y, z],
        "overview_notes": "Preview may select a publisher overview. The z15 detail is about 4.78 m/pixel, finer than the 10 m source, and touches base-resolution blocks. Local and HTTP paths both reference the checksum-pinned 36,000 x 36,000 parent.",
        "traffic_notes": "The instrumented range server reports exact response body bytes and separates HEAD from byte-range GET requests. Warm application-cache repeats produced no HTTP traffic.",
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
    parser.add_argument("--port", type=int, default=5096)
    parser.add_argument("--range-port", type=int, default=5196)
    parser.add_argument("--budget-mib", type=int, default=512)
    args = parser.parse_args()
    run(
        args.binary.resolve(),
        args.out.resolve(),
        args.port,
        args.range_port,
        args.budget_mib * 1024**2,
    )
