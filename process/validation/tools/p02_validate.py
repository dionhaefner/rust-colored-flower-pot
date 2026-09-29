"""Validate P02 RGB, ranges, cache identity, and bounded concurrency."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image, ImageDraw
import psutil
import rasterio
import terracotta as tc
from terracotta.server import create_app

from common import COMMIT, ROOT, write_json
from range_server import RangeServer

SETTINGS = dict(
    DRIVER_PROVIDER="sqlite",
    RASTER_CACHE_SIZE=0,
    USE_MULTIPROCESSING=False,
    RESAMPLING_METHOD="average",
    REPROJECTION_METHOD="linear",
    PNG_COMPRESS_LEVEL=1,
)
RGB_RANGE = "r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D"


def clone_row(connection, table, source_keys, target_keys, path=None):
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    row = list(
        connection.execute(
            f'SELECT * FROM "{table}" WHERE scene=? AND band=?', source_keys
        ).fetchone()
    )
    row[columns.index("scene")], row[columns.index("band")] = target_keys
    if path is not None:
        row[columns.index("path")] = path
    placeholders = ",".join("?" for _ in row)
    connection.execute(f'INSERT INTO "{table}" VALUES ({placeholders})', row)


def create_partial_mask_raster(target):
    source = ROOT / ".cache/corpus/subsets/lagoon-red.tif"
    with rasterio.open(source) as dataset:
        data = dataset.read(1)
        mask = dataset.read_masks(1)
        profile = dataset.profile.copy()
        profile.update(nodata=None, count=1)
        height, width = mask.shape
        # A retained edge-case derivative: real Sentinel-2 pixels and native
        # georeferencing, with a central internal-mask window affecting red only.
        mask[height // 3 : 2 * height // 3, width // 3 : 2 * width // 3] = 0
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(target, "w", **profile) as output:
            output.write(data, 1)
            output.write_mask(mask)


def prepare_database(target, range_port, partial_raster):
    source = ROOT / "process/validation/artifacts/capture-reproduction/reference.sqlite"
    shutil.copy2(source, target)
    with sqlite3.connect(target) as connection:
        for band in ("red", "green", "blue"):
            source_scene = "lagoon"
            clone_row(
                connection,
                "datasets",
                (source_scene, band),
                ("partial", band),
                str(partial_raster) if band == "red" else None,
            )
            clone_row(connection, "metadata", (source_scene, band), ("partial", band))
            remote_path = f"http://127.0.0.1:{range_port}/subsets/lagoon-{band}.tif"
            clone_row(
                connection,
                "datasets",
                ("lagoon", band),
                ("remote", band),
                remote_path,
            )
            clone_row(connection, "metadata", ("lagoon", band), ("remote", band))


def python_references(database, output):
    tc.update_settings(DRIVER_PATH=str(database), **SETTINGS)
    client = create_app().test_client()
    cases = {
        "explicit": f"/rgb/lagoon/preview.png?{RGB_RANGE}",
        "default": "/rgb/lagoon/preview.png?r=red&g=green&b=blue",
        "percentile": "/rgb/lagoon/preview.png?r=red&g=green&b=blue&r_range=%5B%22p2%22%2C%22p98%22%5D&g_range=%5B%22p2%22%2C%22p98%22%5D&b_range=%5B%22p2%22%2C%22p98%22%5D",
        "repeated": "/rgb/lagoon/preview.png?r=red&g=red&b=red&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D",
        "partial-mask": f"/rgb/partial/preview.png?{RGB_RANGE}",
        "non-square": f"/rgb/lagoon/preview.png?{RGB_RANGE}&tile_size=%5B128%2C256%5D",
        "tile": f"/rgb/lagoon/12/2188/1466.png?{RGB_RANGE}",
        "out-of-bounds": f"/rgb/lagoon/20/0/0.png?{RGB_RANGE}&tile_size=%5B128%2C512%5D",
    }
    references = {}
    for name, url in cases.items():
        response = client.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"Python reference {name} returned {response.status_code}")
        path = output / "reference" / f"{name}.png"
        path.write_bytes(response.data)
        references[name] = {"url": url, "path": path}
    return references


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


def fetch(base_url, url, timeout=120):
    try:
        response = urllib.request.urlopen(base_url + url, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    return response.status, response.read()


def rgba(data):
    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGBA")).copy()


def compare_png(expected_path, actual):
    with Image.open(expected_path) as image:
        expected = np.asarray(image.convert("RGBA")).copy()
    candidate = rgba(actual)
    return {
        "passed": expected.shape == candidate.shape and np.array_equal(expected, candidate),
        "shape": list(candidate.shape),
        "different_channels": int(np.count_nonzero(expected != candidate))
        if expected.shape == candidate.shape
        else None,
    }


def metrics(base_url):
    status, body = fetch(base_url, "/__terracotta/metrics")
    if status != 200:
        raise RuntimeError(f"metrics returned {status}")
    return json.loads(body)


def make_sheet(output, results):
    names = ["explicit", "percentile", "repeated", "partial-mask", "tile", "non-square"]
    sheet = Image.new("RGBA", (512, len(names) * 280), "white")
    draw = ImageDraw.Draw(sheet)
    for row, name in enumerate(names):
        for column, path in enumerate(
            (output / "reference" / f"{name}.png", output / "rust" / f"{name}.png")
        ):
            with Image.open(path) as image:
                rendered = image.convert("RGBA")
                sheet.paste(rendered, (column * 256, row * 280), rendered)
        draw.text((4, row * 280 + 258), f"{name}: Python | Rust", fill="black")
    sheet.convert("RGB").save(output / "side-by-side.png")


def run(binary, output, server_port, range_port, budget_bytes):
    if output.exists():
        raise FileExistsError("choose a fresh P02 validation output directory")
    (output / "reference").mkdir(parents=True)
    (output / "rust").mkdir()
    range_server = RangeServer(ROOT / ".cache/corpus", range_port)
    database = output / "p02.sqlite"
    partial_raster = output / "partial-red-internal-mask.tif"
    create_partial_mask_raster(partial_raster)
    prepare_database(database, range_server.port, partial_raster)
    references = python_references(database, output)
    base_url = f"http://127.0.0.1:{server_port}"
    environment = dict(
        os.environ,
        RUST_BACKTRACE="1",
        TC_RASTER_WORKERS="3",
        TC_RASTER_QUEUE_CAPACITY="8",
        TC_RASTER_TIMEOUT_MS="120000",
        TC_RASTER_CACHE_BYTES=str(64 * 1024**2),
    )
    results = []
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
            initial = metrics(base_url)
            ordered = ["repeated", "explicit", "default", "percentile", "partial-mask", "non-square", "tile", "out-of-bounds"]
            for name in ordered:
                status, body = fetch(base_url, references[name]["url"])
                comparison = compare_png(references[name]["path"], body) if status == 200 else {"passed": False}
                (output / "rust" / f"{name}.png").write_bytes(body)
                results.append({"name": name, "status": status, "comparison": comparison, "sha256": hashlib.sha256(body).hexdigest()})
                if name == "repeated":
                    after_repeated = metrics(base_url)
            after_local = metrics(base_url)

            repeated_jobs = after_repeated["submitted"] - initial["submitted"]
            # The first request is repeated RGB and must submit only one unique band.
            repeated_deduplicated = repeated_jobs == 1

            partial = rgba((output / "rust" / "partial-mask.png").read_bytes())
            partial_mask_semantics = bool(
                np.any((partial[..., 0] == 0) & (partial[..., 1] > 0) & (partial[..., 3] == 255))
            )

            remote_before = range_server.snapshot()
            remote_url = f"/rgb/remote/preview.png?{RGB_RANGE}"
            remote_status, remote_body = fetch(base_url, remote_url)
            local_status, local_body = fetch(base_url, f"/rgb/lagoon/preview.png?{RGB_RANGE}")
            remote_first = range_server.snapshot()
            remote_repeat_status, remote_repeat_body = fetch(base_url, remote_url)
            remote_second = range_server.snapshot()
            remote_equivalent = (
                remote_status == local_status == remote_repeat_status == 200
                and np.array_equal(rgba(remote_body), rgba(local_body))
                and np.array_equal(rgba(remote_body), rgba(remote_repeat_body))
            )
            remote_cache_stopped_traffic = remote_second == remote_first

            cold_urls = [
                f"/rgb/lagoon/preview.png?{RGB_RANGE}&tile_size=%5B{240 + index}%2C256%5D"
                for index in range(3)
            ]
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=3) as pool:
                cold = list(pool.map(lambda url: fetch(base_url, url), cold_urls))
            cold_seconds = time.perf_counter() - start
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=3) as pool:
                warm = list(pool.map(lambda url: fetch(base_url, url), cold_urls))
            warm_seconds = time.perf_counter() - start
            concurrency_passed = all(status == 200 for status, _ in cold + warm) and all(
                np.array_equal(rgba(left[1]), rgba(right[1])) for left, right in zip(cold, warm)
            )
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
        all(item["status"] == 200 and item["comparison"]["passed"] for item in results)
        and repeated_deduplicated
        and partial_mask_semantics
        and remote_equivalent
        and remote_first["range_requests"] > remote_before["range_requests"]
        and remote_first["bytes_sent"] > remote_before["bytes_sent"]
        and remote_cache_stopped_traffic
        and concurrency_passed
        and final_metrics["peak_in_flight"] <= final_metrics["workers"]
        and final_metrics["open_file_descriptors"]
        <= initial["open_file_descriptors"] + final_metrics["workers"] * 4
        and peak[0] <= budget_bytes
    )
    report = {
        "passed": passed,
        "reference_commit": COMMIT,
        "results": results,
        "repeated_band_deduplicated": repeated_deduplicated,
        "partial_mask_semantics": partial_mask_semantics,
        "remote": {
            "equivalent_to_local": remote_equivalent,
            "before": remote_before,
            "after_first": remote_first,
            "after_cached_repeat": remote_second,
            "cached_repeat_added_no_traffic": remote_cache_stopped_traffic,
        },
        "concurrency": {
            "passed": concurrency_passed,
            "requests": len(cold_urls),
            "cold_seconds": cold_seconds,
            "warm_seconds": warm_seconds,
        },
        "resources": {
            "budget_bytes": budget_bytes,
            "sampled_peak_rss_bytes": peak[0],
            "handles_bounded": final_metrics["open_file_descriptors"]
            <= initial["open_file_descriptors"] + final_metrics["workers"] * 4,
            "metrics_initial": initial,
            "metrics_after_repeated": after_repeated,
            "metrics_after_local": after_local,
            "metrics_final": final_metrics,
        },
        "comparison": "Exact decoded RGBA against the frozen Python handler; PNG streams are not compared.",
        "visual_check": "side-by-side.png contains six Python/Rust pairs at native display scale.",
    }
    write_json(output / "report.json", report)
    make_sheet(output, results)
    database.unlink()
    partial_raster.unlink()
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/terracotta")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5094)
    parser.add_argument("--range-port", type=int, default=5194)
    parser.add_argument("--budget-mib", type=int, default=512)
    args = parser.parse_args()
    run(
        args.binary.resolve(),
        args.out.resolve(),
        args.port,
        args.range_port,
        args.budget_mib * 1024**2,
    )
