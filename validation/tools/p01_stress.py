"""Exercise Rust P01 against the unaltered full WorldCover COG under an RSS budget."""
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

from common import ROOT, CACHE, read_json, sha256, write_json


def prepare_database(source, target):
    reference = ROOT / "validation/artifacts/capture-reproduction/reference.sqlite"
    shutil.copy2(reference, target)
    with rasterio.open(source) as dataset:
        bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds)
        source_info = {"width": dataset.width, "height": dataset.height, "dtype": dataset.dtypes[0], "overviews": dataset.overviews(1), "bounds_wgs84": list(bounds), "block_shapes": [list(shape) for shape in dataset.block_shapes]}
    with sqlite3.connect(target) as connection:
        connection.execute("UPDATE datasets SET path=? WHERE scene='lagoon' AND band='classes'", (str(source),))
        # Preserve the legacy W,S,E,N positional mapping despite misleading names.
        connection.execute("UPDATE metadata SET bounds_north=?, bounds_east=?, bounds_south=?, bounds_west=? WHERE scene='lagoon' AND band='classes'", bounds)
    return source_info


def wait_ready(base_url, process):
    for _ in range(400):
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with {process.returncode}")
        try:
            urllib.request.urlopen(base_url + "/keys", timeout=.2).read(); return
        except Exception:
            time.sleep(.05)
    raise TimeoutError("server did not become ready")


def compare_image(candidate, reference):
    with Image.open(candidate) as left, Image.open(reference) as right:
        a = np.asarray(left.convert("RGBA")); b = np.asarray(right.convert("RGBA"))
    return {"passed": a.shape == b.shape and np.array_equal(a, b), "shape": list(a.shape), "different_channels": int(np.count_nonzero(a != b)) if a.shape == b.shape else None}


def run(binary, out, port, budget):
    if out.exists():
        raise FileExistsError("choose a fresh P01 stress output directory")
    out.mkdir(parents=True)
    source = (CACHE / "worldcover.tif").resolve()
    expected_hash = read_json(ROOT / "validation/reference/assets.json")["assets"][-1]["sha256"]
    actual_hash = sha256(source)
    if actual_hash != expected_hash:
        raise RuntimeError("full WorldCover source checksum changed")
    source_info = prepare_database(source, out / "stress.sqlite")
    views = read_json(ROOT / "validation/reference/capture/views.json")
    palette_query = next(view["url"].split("?", 1)[1] for view in views if view["id"] == "landcover-preview")
    python_stress = read_json(ROOT / "validation/artifacts/stress/stress.json")
    x, y, z = python_stress["detail_xyz"]
    cases = [
        ("preview", f"/singleband/lagoon/classes/preview.png?{palette_query}"),
        ("detail", f"/singleband/lagoon/classes/{z}/{x}/{y}.png?{palette_query}"),
    ]
    base_url = f"http://127.0.0.1:{port}"
    with (out / "server.log").open("w") as log:
        process = subprocess.Popen([str(binary), "serve", "-d", str(out / "stress.sqlite"), "--bind", f"127.0.0.1:{port}"], cwd=ROOT, env=dict(os.environ, RUST_BACKTRACE="1"), stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_ready(base_url, process)
            measured = psutil.Process(process.pid)
            io_before = measured.io_counters()
            peak = [measured.memory_info().rss]
            finished = threading.Event()
            def sample():
                while not finished.wait(.005):
                    try: peak[0] = max(peak[0], measured.memory_info().rss)
                    except psutil.NoSuchProcess: return
            sampler = threading.Thread(target=sample, daemon=True); sampler.start()
            results = []
            for name, url in cases:
                started = time.perf_counter()
                data = urllib.request.urlopen(base_url + url, timeout=120).read()
                elapsed = time.perf_counter() - started
                path = out / f"{name}.png"; path.write_bytes(data)
                comparison = compare_image(path, ROOT / "validation/artifacts/stress" / f"{name}.png")
                results.append({"name": name, "seconds": elapsed, "bytes": len(data), "comparison": comparison, "url": url})
            finished.set(); sampler.join()
            peak[0] = max(peak[0], measured.memory_info().rss)
            io_after = measured.io_counters()
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
    io_delta = {field: getattr(io_after, field) - getattr(io_before, field) for field in io_before._fields}
    report = {
        "passed": peak[0] <= budget and all(item["comparison"]["passed"] for item in results),
        "budget_bytes": budget,
        "sampled_peak_rss_bytes": peak[0],
        "budget_exceeded": peak[0] > budget,
        "source": {**source_info, "path": str(source), "file_bytes": source.stat().st_size, "sha256": actual_hash, "tiff_variant": "classic TIFF / publisher COG", "decoded_value_bytes": source_info["width"] * source_info["height"]},
        "results": results,
        "io_delta": io_delta,
        "detail_xyz": [x, y, z],
        "overview_notes": "Broad preview may select one of the publisher COG's native overviews. The z15 request is about 4.78 m/pixel, finer than the 10 m source, and therefore touches base-resolution blocks. The source path is the checksum-pinned 36,000 x 36,000 parent, not a derivative.",
        "io_notes": "Linux process counters cover the Rust server. read_chars counts cached read syscalls; read_bytes counts physical storage I/O and may be zero after checksum verification warms the OS cache. No remote traffic is claimed.",
        "database_note": "stress.sqlite was a generated copy of the Python-created database with only the categorical path/bounds redirected to the pinned full parent; it is removed after the run.",
    }
    (out / "stress.sqlite").unlink()
    write_json(out / "report.json", report)
    print(json.dumps(report, indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/terracotta")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5092)
    parser.add_argument("--budget-mib", type=int, default=512)
    args = parser.parse_args()
    run(args.binary.resolve(), args.out.resolve(), args.port, args.budget_mib * 1024**2)
