"""Measure real-raster viewport, pan, zoom and revisit delivery on one VM.

Inputs are checksum-pinned full scenes. The optional range server measures
controlled latency and traffic. Real S3 performance requires separate checks.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import io
import json
import math
import os
import platform
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def native(binary, folder, env, request):
    instruction = folder / "operation.json"
    instruction.write_text(json.dumps(request))
    result = subprocess.run(
        [str(binary), "data-operation", str(instruction)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def quantiles(values):
    return {
        name: float(np.percentile(values, p)) if values else None
        for name, p in (("p50", 50), ("p95", 95), ("p99", 99))
    }


def tile(lon, lat, zoom):
    n = 2**zoom
    return (
        int((lon + 180) / 360 * n),
        int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n),
    )


@contextlib.contextmanager
def range_server(paths, latency_ms):
    versions = {name: '"' + digest(path) + '"' for name, path in paths.items()}
    counts = {
        "gets": 0,
        "heads": 0,
        "range_gets": 0,
        "bytes": 0,
        "conditional_gets": 0,
    }
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, *_args):
            pass

        def respond(self, body):
            path = paths.get(self.path)
            if path is None:
                self.send_error(404)
                return
            version = versions[self.path]
            if self.headers.get("If-Match", version) != version:
                self.send_error(412)
                return
            size = path.stat().st_size
            start, stop = 0, size - 1
            header = self.headers.get("Range")
            if header:
                try:
                    first, last = header.removeprefix("bytes=").split("-")
                    start, stop = int(first), min(int(last) if last else stop, stop)
                except ValueError:
                    self.send_error(416)
                    return
                if start > stop or start < 0:
                    self.send_error(416)
                    return
            time.sleep(latency_ms / 1000)
            self.send_response(206 if header else 200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "image/tiff")
            self.send_header("Content-Length", str(stop - start + 1))
            self.send_header("ETag", version)
            if header:
                self.send_header("Content-Range", f"bytes {start}-{stop}/{size}")
            self.end_headers()
            with lock:
                counts["gets" if body else "heads"] += 1
                counts["range_gets"] += int(body and bool(header))
                counts["conditional_gets"] += int(body and "If-Match" in self.headers)
                counts["bytes"] += stop - start + 1 if body else 0
            if body:
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = stop - start + 1
                    while remaining:
                        data = stream.read(min(remaining, 1024 * 1024))
                        self.wfile.write(data)
                        remaining -= len(data)

        def do_GET(self):
            self.respond(True)

        def do_HEAD(self):
            self.respond(False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", counts
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextlib.contextmanager
def service(binary, database, env, folder, cpus):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = [str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{port}"]
    if cpus:
        command = ["taskset", "-c", ",".join(map(str, cpus)), *command]
    with (folder / "server.log").open("w") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        origin = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    with urlopen(origin + "/keys", timeout=1):
                        break
                except (URLError, TimeoutError):
                    if process.poll() is not None:
                        raise RuntimeError("native server exited; inspect server.log")
                    time.sleep(0.025)
            else:
                raise TimeoutError("native server did not start")
            yield origin, process
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def fetch(origin, route, image_dir):
    started = time.perf_counter()
    try:
        with urlopen(origin + route, timeout=60) as response:
            status, payload = response.status, response.read()
    except HTTPError as error:
        status, payload = error.code, error.read()
    elapsed = (time.perf_counter() - started) * 1000
    row = {"route": route, "status": status, "http_ms": elapsed, "bytes": len(payload)}
    if status == 200:
        image = Image.open(io.BytesIO(payload)).convert("RGBA")
        row["decoded_sha256"] = hashlib.sha256(image.tobytes()).hexdigest()
        row["dimensions"] = list(image.size)
        row["valid_pixels"] = int(np.count_nonzero(np.asarray(image)[:, :, 3]))
        name = hashlib.sha256(route.encode()).hexdigest()
        (image_dir / f"{name}.png").write_bytes(payload)
    else:
        row["error"] = payload.decode(errors="replace")[:500]
    return row


def route(scene, mode, zoom, x, y, rgb_format="png", webp_quality=85):
    if mode == "rgb":
        path = f"/rgb/{scene['id']}/{zoom}/{x}/{y}.png"
        parameters = {"r": "red", "g": "green", "b": "blue"}
        if rgb_format == "webp":
            parameters.update(format="webp", quality=webp_quality)
        for name in ("r", "g", "b"):
            parameters[name + "_range"] = json.dumps(scene["range"])
        for name, band in zip(("r", "g", "b"), ("red", "green", "blue")):
            if scene.get("band_indexes"):
                parameters[name + "_band"] = scene["band_indexes"][band]
    else:
        path = f"/singleband/{scene['id']}/red/{zoom}/{x}/{y}.png"
        parameters = {"stretch_range": json.dumps(scene["range"])}
        if scene.get("band_indexes"):
            parameters["band"] = scene["band_indexes"]["red"]
        if scene.get("categorical"):
            parameters = {
                "colormap": "explicit",
                "explicit_color_map": json.dumps(
                    {
                        value: [value * 2 % 256, value * 3 % 256, value * 5 % 256, 255]
                        for value in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
                    }
                ),
            }
    return path + "?" + urlencode(parameters)


def run_case(args, scene, mode, storage, users, overlap, trial, env, sources, metadata):
    name = f"{scene['id']}-{mode}-{storage}-{users}-{overlap}-{trial}"
    folder = args.out / name
    folder.mkdir()
    images = folder / "images"
    images.mkdir()
    paths = {"/" + key + ".tif": value for key, value in sources.items()}
    remote = (
        range_server(paths, args.latency_ms)
        if storage == "range"
        else contextlib.nullcontext((None, None))
    )
    with remote as (remote_origin, traffic):
        database = folder / "catalog.sqlite"
        native(
            args.binary,
            folder,
            env,
            {
                "operation": "driver_create",
                "path": str(database),
                "keys": ["scene", "band"],
            },
        )
        for band, source in scene["bands"].items():
            path = str(sources[source])
            if storage == "range":
                path = remote_origin + "/" + source + ".tif"
            elif storage == "s3-public":
                path = args.manifest_data["sources"][source]["url"]
            native(
                args.binary,
                folder,
                env,
                {
                    "operation": "insert",
                    "path": str(database),
                    "keys": [scene["id"], band],
                    "raster": path,
                    "metadata": metadata[source],
                },
            )
        steps, requests = [], []
        with service(args.binary, database, env, folder, args.cpus) as (
            origin,
            process,
        ):
            for zoom_index, zoom in enumerate(scene["zooms"]):
                seen = [set() for _ in range(users)]
                for step, offset in enumerate((0, 1, 2, 3, 0)):
                    before = dict(traffic) if traffic else None
                    routes = []
                    for user in range(users):
                        bounds = scene.get(
                            "navigation_bounds",
                            metadata[next(iter(scene["bands"].values()))]["bounds"],
                        )
                        lon, lat = scene["center"]
                        shift = (
                            0
                            if overlap
                            else (
                                0
                                if user == 0
                                else (1 if user % 2 else -1) * ((user + 1) // 2)
                            )
                        )
                        user_x, user_y = tile(
                            lon + shift * (bounds[2] - bounds[0]) * 0.1, lat, zoom
                        )
                        viewport = {
                            route(
                                scene,
                                mode,
                                zoom,
                                user_x + offset + dx,
                                user_y + dy,
                                args.rgb_format,
                                args.webp_quality,
                            )
                            for dy in range(-1, 2)
                            for dx in range(-1, 2)
                        }
                        wanted = viewport if step == 4 else viewport - seen[user]
                        seen[user] = viewport
                        routes.extend(sorted(wanted))
                    started = time.perf_counter()
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=args.parallel * users
                    ) as pool:
                        rows = list(
                            pool.map(lambda url: fetch(origin, url, images), routes)
                        )
                    elapsed = (time.perf_counter() - started) * 1000
                    current = json.load(urlopen(origin + "/__terracotta/metrics"))
                    if (
                        args.require_tiff_pixels
                        and current["cost"]["raster_backend_reads"]["gdal"]
                    ):
                        raise AssertionError("TIFF-required workload used GDAL pixels")
                    requests.extend(rows)
                    steps.append(
                        {
                            "zoom": zoom,
                            "kind": ("first" if zoom_index == 0 else "zoom")
                            if step == 0
                            else "revisit"
                            if step == 4
                            else "pan",
                            "completion_ms": elapsed,
                            "requests": len(rows),
                            "failures": sum(r["status"] != 200 for r in rows),
                            "latency_ms": quantiles([r["http_ms"] for r in rows]),
                            "requests_per_second": len(rows) * 1000 / elapsed,
                            "completed_raster_jobs": current["completed"],
                            "raster_backend_reads": current["cost"][
                                "raster_backend_reads"
                            ],
                            "source_traffic": {
                                key: traffic[key] - before[key] for key in before
                            }
                            if before
                            else None,
                        }
                    )
            metrics = json.load(urlopen(origin + "/__terracotta/metrics"))
            if (
                args.require_tiff_pixels
                and not metrics["cost"]["raster_backend_reads"]["tiff"]
            ):
                raise AssertionError(
                    "TIFF-required workload did not read raster pixels"
                )
            resources = {}
            status_file = Path(f"/proc/{process.pid}/status")
            if status_file.exists():
                for line in status_file.read_text().splitlines():
                    if line.startswith(("VmHWM:", "VmRSS:", "Threads:")):
                        key, value = line.split(":", 1)
                        resources[key] = value.strip()
            stat_file = Path(f"/proc/{process.pid}/stat")
            if stat_file.exists():
                fields = stat_file.read_text().split(")", 1)[1].split()
                resources["cpu_seconds"] = (
                    int(fields[11]) + int(fields[12])
                ) / os.sysconf("SC_CLK_TCK")
        result = {
            "case": name,
            "scene": scene["id"],
            "mode": mode,
            "storage": storage,
            "users": users,
            "overlap": overlap,
            "trial": trial,
            "steps": steps,
            "requests": requests,
            "metrics": metrics,
            "resources": resources,
            "source_traffic": dict(traffic) if traffic else None,
        }
        (folder / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "case": name,
                    "first_ms": quantiles(
                        [s["completion_ms"] for s in steps if s["kind"] == "first"]
                    ),
                    "pan_ms": quantiles(
                        [s["completion_ms"] for s in steps if s["kind"] == "pan"]
                    ),
                    "failures": sum(s["failures"] for s in steps),
                }
            ),
            flush=True,
        )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/fixtures/navigation.json"
    )
    parser.add_argument("--sources", type=Path, default=ROOT / ".cache/corpus")
    parser.add_argument(
        "--storage",
        nargs="+",
        choices=("local", "range", "s3-public"),
        default=["local", "range"],
    )
    parser.add_argument("--scenes", nargs="+")
    parser.add_argument("--users", nargs="+", type=int, default=[1, 3])
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--latency-ms", type=float, default=15)
    parser.add_argument("--rgb-format", choices=("png", "webp"), default="png")
    parser.add_argument("--webp-quality", type=int, default=85)
    parser.add_argument("--setting", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument(
        "--require-tiff-pixels",
        action="store_true",
        help="Fail if any pixel read reaches GDAL; requires at least one raster job",
    )
    args = parser.parse_args()
    if min(args.trials, args.parallel, args.workers, args.cores, *args.users) < 1:
        parser.error("counts must be positive")
    args.binary, args.out = args.binary.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    args.manifest_data = json.loads(args.manifest.read_text())
    sources = {
        key: (args.sources / row["file"]).resolve()
        for key, row in args.manifest_data["sources"].items()
    }
    for key, path in sources.items():
        if digest(path) != args.manifest_data["sources"][key]["sha256"]:
            raise ValueError(f"source checksum mismatch: {key}")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TC_", "RCFP_COMPAT_"))
    }
    env.update(
        RCFP_COMPAT_PROFILE="native",
        GDAL_CACHEMAX="64",
        TC_RASTER_WORKERS=str(args.workers),
        TC_RASTER_QUEUE_CAPACITY="128",
        TC_RASTER_CACHE_BYTES=str(64 * 1024**2),
        TC_SOURCE_BLOCK_CACHE_BYTES="0",
        TC_EXPOSE_METRICS="true",
    )
    env.update(dict(item.split("=", 1) for item in args.setting))
    args.cpus = (
        sorted(os.sched_getaffinity(0))[: args.cores]
        if hasattr(os, "sched_getaffinity")
        else []
    )
    metadata = {
        key: native(
            args.binary,
            args.out,
            env,
            {
                "operation": "compute_metadata",
                "path": str(path),
                "options": {"max_shape": [512, 512]},
            },
        )
        for key, path in sources.items()
    }
    report = {
        "binary_sha256": digest(args.binary),
        "platform": platform.platform(),
        "cpus": args.cpus,
        "manifest_sha256": digest(args.manifest),
        "rgb_format": args.rgb_format,
        "webp_quality": args.webp_quality,
        "settings": {
            key: value
            for key, value in env.items()
            if (key.startswith("TC_") or key == "GDAL_CACHEMAX")
            and not any(
                sensitive in key
                for sensitive in ("KEY", "SECRET", "TOKEN", "PASSWORD", "GDAL_OPTIONS")
            )
        },
        "gdal_read_options": {
            key: value
            for key, value in json.loads(env.get("TC_GDAL_OPTIONS", "{}")).items()
            if key
            in (
                "CPL_VSIL_CURL_CHUNK_SIZE",
                "CPL_VSIL_CURL_CACHE_SIZE",
                "GDAL_INGESTED_BYTES_AT_OPEN",
            )
        },
        "limits": [
            "fresh process with warm OS page cache; no privileged cold-disk eviction",
            "range server is a controlled HTTP surrogate, not real S3",
            "HTTP completion plus image decoding; browser paint unavailable",
            "public S3 request/byte counts unavailable without origin instrumentation",
        ],
        "cases": [],
    }
    for trial in range(args.trials):
        for scene in args.manifest_data["scenes"]:
            if args.scenes and scene["id"] not in args.scenes:
                continue
            for mode in (
                ["singleband", "rgb"] if len(scene["bands"]) == 3 else ["singleband"]
            ):
                for storage in args.storage:
                    for users in args.users:
                        for overlap in [True, False] if users > 1 else [True]:
                            report["cases"].append(
                                run_case(
                                    args,
                                    scene,
                                    mode,
                                    storage,
                                    users,
                                    overlap,
                                    trial,
                                    env,
                                    sources,
                                    metadata,
                                )
                            )
                            (args.out / "report.json").write_text(
                                json.dumps(report, indent=2) + "\n"
                            )
    if any(r["status"] != 200 for case in report["cases"] for r in case["requests"]):
        raise SystemExit("benchmark recorded failed requests")


if __name__ == "__main__":
    main()
