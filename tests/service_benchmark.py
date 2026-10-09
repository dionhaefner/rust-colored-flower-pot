"""Compare complete RCFP, Terracotta and TiTiler navigation on pinned rasters."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
import platform
import socket
import statistics
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import psutil
from navigation_benchmark import (
    ROOT,
    digest,
    native,
    quantiles,
    range_server,
    route,
    tile,
)
from PIL import Image

ENGINES = ("rcfp", "terracotta", "titiler")
MIB = 1024**2
COLORS = {
    value: [value * 2 % 256, value * 3 % 256, value * 5 % 256, 255]
    for value in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
}


def cpu_allocation():
    available = sorted(os.sched_getaffinity(0))

    def identity(cpu):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        return tuple(
            (topology / name).read_text().strip()
            for name in ("physical_package_id", "core_id")
        )

    serving, identities = [], set()
    for cpu in available:
        physical = identity(cpu)
        if physical not in identities:
            serving.append(cpu)
            identities.add(physical)
        if len(serving) == 4:
            break
    if len(serving) != 4:
        raise RuntimeError("This comparison requires four physical CPU cores")
    clients = [cpu for cpu in available if identity(cpu) not in identities]
    return serving, clients


def parameters(engine, scene, mode, zoom, x, y, style=False, expression=False):
    selected = dict(scene)
    if style:
        lo, hi = scene["range"]
        selected["range"] = [lo + (hi - lo) * 0.1, lo + (hi - lo) * 0.8]
    if engine != "titiler":
        if expression:
            return f"/compute/{scene['id']}/{zoom}/{x}/{y}.png?" + urlencode(
                {
                    "expression": "v1/v2",
                    "v1": "nir",
                    "v2": "red",
                    "stretch_range": "[0,2]",
                }
            )
        return route(selected, mode, zoom, x, y)
    assets = (
        ["nir", "red"]
        if expression
        else (["red", "green", "blue"] if mode == "rgb" else ["red"])
    )
    query = [
        ("scene", scene["id"]),
        ("tilesize", "256"),
        ("resampling", "nearest" if scene.get("categorical") else "bilinear"),
        ("reproject", "nearest" if scene.get("categorical") else "bilinear"),
    ]
    query.extend(("assets", name + "|bidx=1") for name in assets)
    if scene.get("categorical"):
        query.append(("colormap", json.dumps(COLORS)))
    else:
        query.append(
            ("rescale", "0,2" if expression else ",".join(map(str, selected["range"])))
        )
    if expression:
        query.append(("expression", "b1/b2"))
    return f"/tiles/WebMercatorQuad/{zoom}/{x}/{y}.png?" + urlencode(query)


def request(origin, path):
    started = time.perf_counter()
    try:
        with urlopen(origin + path, timeout=60) as response:
            status, payload = response.status, response.read()
            headers = dict(response.headers)
        error = None
    except HTTPError as exc:
        status, payload, headers = exc.code, exc.read(), {}
        error = payload.decode(errors="replace")[:300]
    except (URLError, TimeoutError, OSError) as exc:
        status, payload, headers, error = 0, b"", {}, str(exc)
    return {
        "route": path,
        "status": status,
        "http_ms": (time.perf_counter() - started) * 1000,
        "bytes": len(payload),
        "error": error,
        "headers": headers,
    }, payload


def process_resources(pid):
    root = psutil.Process(pid)
    processes = [root, *root.children(recursive=True)]
    result = {
        "pss_bytes": 0,
        "uss_bytes": 0,
        "rss_sum_bytes": 0,
        "cpu_seconds": 0.0,
        "processes": 0,
        "threads": 0,
        "fds": 0,
    }
    for process in processes:
        try:
            memory = process.memory_full_info()
            cpu = process.cpu_times()
            result["pss_bytes"] += memory.pss
            result["uss_bytes"] += memory.uss
            result["rss_sum_bytes"] += memory.rss
            result["cpu_seconds"] += cpu.user + cpu.system
            result["processes"] += 1
            result["threads"] += process.num_threads()
            result["fds"] += process.num_fds()
        except psutil.NoSuchProcess:
            continue
    return result


@contextlib.contextmanager
def server(args, engine, database, folder, categorical):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TC_", "RCFP_COMPAT_"))
    }
    env.update(
        TC_DRIVER_PATH=str(database),
        TC_USE_MULTIPROCESSING="false",
        TC_PNG_COMPRESS_LEVEL="1",
        GDAL_NUM_THREADS="1",
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        VSI_CACHE="FALSE",
        TC_RESAMPLING_METHOD="nearest" if categorical else "linear",
        TC_REPROJECTION_METHOD="nearest" if categorical else "linear",
        CPL_VSIL_CURL_CHUNK_SIZE="16384",
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        NUMEXPR_NUM_THREADS="1",
    )
    if engine == "rcfp":
        env.update(
            RCFP_COMPAT_PROFILE="native",
            GDAL_CACHEMAX="64",
            TC_RASTER_WORKERS="4",
            TC_RASTER_QUEUE_CAPACITY="128",
            TC_RASTER_CACHE_BYTES=str(64 * MIB),
            TC_SOURCE_BLOCK_CACHE_BYTES="0",
            TC_RENDER_CACHE_BYTES="0",
            TC_REMOTE_SOURCE_CACHE_TTL_SECONDS=str(args.rcfp_source_ttl),
            TC_PREFETCH_TILE_RING="0",
            TC_EXPOSE_METRICS="true",
            TC_GDAL_OPTIONS=json.dumps(
                {
                    "CPL_VSIL_CURL_CACHE_SIZE": str(32 * MIB),
                    "CPL_VSIL_CURL_CHUNK_SIZE": "16384",
                }
            ),
        )
        command = [
            str(args.binary),
            "serve",
            "-d",
            str(database),
            "--bind",
            f"127.0.0.1:{port}",
        ]
        ready = "/keys"
    elif engine == "terracotta":
        env.update(
            GDAL_CACHEMAX="16",
            CPL_VSIL_CURL_CACHE_SIZE=str(8 * MIB),
            TC_RASTER_CACHE_SIZE=str(16 * MIB),
        )
        command = [
            str(args.reference_python),
            "-m",
            "gunicorn",
            "--workers",
            "4",
            "--preload",
            "--timeout",
            "90",
            "--bind",
            f"127.0.0.1:{port}",
            "terracotta.server:create_app()",
        ]
        ready = "/keys"
    else:
        env.update(GDAL_CACHEMAX="32", CPL_VSIL_CURL_CACHE_SIZE=str(8 * MIB))
        command = [
            str(args.titiler_python),
            "-m",
            "uvicorn",
            "benchmark_titiler_app:app",
            "--app-dir",
            str(ROOT / "tests"),
            "--workers",
            "4",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ]
        ready = "/health"
    command = ["taskset", "-c", ",".join(map(str, args.cpus)), *command]
    with (folder / "server.log").open("w") as log:
        started = time.perf_counter()
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        origin = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    with urlopen(origin + ready, timeout=1):
                        break
                except (URLError, TimeoutError):
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"{engine} failed to start: {folder / 'server.log'}"
                        )
                    time.sleep(0.05)
            else:
                raise TimeoutError(f"{engine} startup timeout")
            # A readiness response can precede other workers' imports. Include
            # the wait for all four workers in startup without reading rasters.
            if engine == "terracotta":
                while time.monotonic() < deadline:
                    children = psutil.Process(process.pid).children()
                    # Uvicorn additionally owns a resource-tracker process.
                    workers = [
                        p
                        for p in children
                        if "resource_tracker" not in " ".join(p.cmdline())
                    ]
                    if len(workers) == 4:
                        break
                    time.sleep(0.05)
                else:
                    raise TimeoutError("Not all Terracotta workers became ready")
            elif engine == "titiler":
                ready_workers = set()
                while time.monotonic() < deadline:
                    with urlopen(origin + "/health", timeout=1) as response:
                        ready_workers.add(json.load(response)["pid"])
                    if len(ready_workers) == 4:
                        break
                    time.sleep(0.01)
                else:
                    raise TimeoutError("Not all TiTiler workers became ready")
            yield origin, process, (time.perf_counter() - started) * 1000, env
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def workloads(scene, users, overlap, metadata, include_extra):
    bounds = metadata[next(iter(scene["bands"].values()))]["bounds"]
    for zi, zoom in enumerate(scene["zooms"]):
        seen = [set() for _ in range(users)]
        for step, offset in enumerate((0, 1, 2, 3, 0)):
            coordinates = []
            for user in range(users):
                shift = 0 if overlap or user == 0 else (1 if user % 2 else -1)
                x, y = tile(
                    scene["center"][0] + shift * (bounds[2] - bounds[0]) * 0.1,
                    scene["center"][1],
                    zoom,
                )
                view = {
                    (zoom, x + offset + dx, y + dy)
                    for dy in range(-1, 2)
                    for dx in range(-1, 2)
                }
                coordinates.extend(sorted(view if step == 4 else view - seen[user]))
                seen[user] = view
            kind = (
                ("first" if zi == 0 else "zoom")
                if step == 0
                else ("revisit" if step == 4 else "pan")
            )
            yield kind, coordinates, False, False
    if include_extra and not scene.get("categorical"):
        yield "style", coordinates, True, False
        if "nir" in scene["bands"]:
            yield "expression", coordinates, False, True


def run_case(
    args, engine, scene, mode, storage, users, overlap, trial, sources, metadata
):
    name = f"{scene['id']}-{mode}-{storage}-{users}-{'overlap' if overlap else 'independent'}-{trial}-{engine}"
    folder = args.out / name
    folder.mkdir()
    images = folder / "images"
    images.mkdir()
    remote = (
        range_server(
            {"/" + key + ".tif": path for key, path in sources.items()}, args.latency_ms
        )
        if storage == "range"
        else contextlib.nullcontext((None, None))
    )
    env = dict(os.environ)
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
            path = (
                remote_origin + "/" + source + ".tif"
                if remote_origin
                else str(sources[source])
            )
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
        result = {
            "engine": engine,
            "scene": scene["id"],
            "mode": mode,
            "storage": storage,
            "users": users,
            "overlap": overlap,
            "trial": trial,
            "steps": [],
            "requests": [],
        }
        with server(
            args, engine, database, folder, scene.get("categorical", False)
        ) as (origin, process, startup, settings):
            result["startup_ms"] = startup
            result["settings"] = {
                k: v
                for k, v in settings.items()
                if k.startswith(("TC_", "GDAL_", "CPL_")) and k != "TC_DRIVER_PATH"
            }
            initial = process_resources(process.pid)
            peaks = dict(initial)
            stop = threading.Event()

            def sample():
                while not stop.wait(0.1):
                    for key, value in process_resources(process.pid).items():
                        peaks[key] = max(peaks[key], value)

            monitor = threading.Thread(target=sample, daemon=True)
            monitor.start()
            try:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=4 * users
                ) as pool:
                    for kind, coordinates, style, expression in workloads(
                        scene, users, overlap, metadata, not args.navigation_only
                    ):
                        if expression and mode == "rgb":
                            continue
                        started = time.perf_counter()
                        futures = [
                            pool.submit(
                                request,
                                origin,
                                parameters(
                                    engine, scene, mode, *xyz, style, expression
                                ),
                            )
                            for xyz in coordinates
                        ]
                        responses = [future.result() for future in futures]
                        receipt = (time.perf_counter() - started) * 1000
                        decode_started = time.perf_counter()
                        rows = []
                        for xyz, (row, body) in zip(coordinates, responses):
                            row.update(xyz=xyz, kind=kind)
                            if row["status"] == 200:
                                try:
                                    image = Image.open(io.BytesIO(body)).convert("RGBA")
                                    array = np.asarray(image)
                                    row.update(
                                        dimensions=list(image.size),
                                        valid_pixels=int(
                                            np.count_nonzero(array[:, :, 3])
                                        ),
                                        decoded_sha256=hashlib.sha256(
                                            image.tobytes()
                                        ).hexdigest(),
                                    )
                                    if image.size != (256, 256):
                                        row["error"] = "Unexpected image dimensions"
                                    identifier = hashlib.sha256(
                                        f"{kind}-{xyz}".encode()
                                    ).hexdigest()
                                    (images / f"{identifier}.png").write_bytes(body)
                                    row["image"] = f"images/{identifier}.png"
                                except (OSError, ValueError) as exc:
                                    row["error"] = f"Image decode: {exc}"
                            rows.append(row)
                        result["requests"].extend(rows)
                        result["steps"].append(
                            {
                                "kind": kind,
                                "receipt_ms": receipt,
                                "decode_and_record_ms": (
                                    time.perf_counter() - decode_started
                                )
                                * 1000,
                                "requests": len(rows),
                                "failures": sum(
                                    row["status"] != 200 or bool(row["error"])
                                    for row in rows
                                ),
                                "latency_ms": quantiles(
                                    [row["http_ms"] for row in rows]
                                ),
                                "source_traffic_cumulative": dict(traffic)
                                if traffic
                                else None,
                            }
                        )
            finally:
                stop.set()
                monitor.join()
            result["resources_start"] = initial
            result["resources_end"] = process_resources(process.pid)
            for key, value in result["resources_end"].items():
                peaks[key] = max(peaks[key], value)
            result["resources_peak"] = peaks
            if engine == "rcfp":
                result["metrics"] = json.load(urlopen(origin + "/__terracotta/metrics"))
        result["source_traffic"] = dict(traffic) if traffic else None
    result["folder"] = name
    (folder / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "case": name,
                "first_ms": result["steps"][0]["receipt_ms"],
                "failures": sum(s["failures"] for s in result["steps"]),
            }
        ),
        flush=True,
    )
    return result


def environment(python, packages):
    code = (
        "import json,rasterio;from importlib.metadata import version;print(json.dumps({'packages':{n:version(n) for n in "
        + repr(packages)
        + "},'gdal':rasterio.__gdal_version__}))"
    )
    result = subprocess.run(
        [str(python), "-c", code], check=True, text=True, capture_output=True
    )
    return json.loads(result.stdout)


def summarize(report, out):
    groups = []
    cases = report["cases"]
    for storage in sorted({c["storage"] for c in cases}):
        for mode in ("singleband", "rgb"):
            for kind in ("first", "pan", "zoom", "revisit", "style", "expression"):
                cells = {}
                for case in cases:
                    if case["storage"] != storage or case["mode"] != mode:
                        continue
                    steps = [s for s in case["steps"] if s["kind"] == kind]
                    if not steps:
                        continue
                    identity = (case["scene"], case["users"], case["overlap"])
                    cells.setdefault(identity, {}).setdefault(
                        case["engine"], []
                    ).append(statistics.median(s["receipt_ms"] for s in steps))
                if not cells:
                    continue
                values = {engine: [] for engine in ENGINES}
                ratios = {engine: [] for engine in ENGINES[1:]}
                for identity, results in cells.items():
                    medians = {
                        engine: statistics.median(results[engine]) for engine in ENGINES
                    }
                    for engine in ENGINES:
                        values[engine].append(medians[engine])
                    for engine, values_for_engine in ratios.items():
                        values_for_engine.append(medians[engine] / medians["rcfp"])
                groups.append(
                    {
                        "storage": storage,
                        "mode": mode,
                        "kind": kind,
                        "cells": len(cells),
                        "median_viewport_ms": {
                            e: statistics.median(v) for e, v in values.items()
                        },
                        "paired_speedup": {
                            e: statistics.median(v) for e, v in ratios.items()
                        },
                    }
                )
    summary = {
        "groups": groups,
        "requests": len([r for c in cases for r in c["requests"]]),
        "failures": sum(s["failures"] for c in cases for s in c["steps"]),
        "per_engine": {},
        "valid_timing_comparison": not any(
            s["failures"] for c in cases for s in c["steps"]
        ),
    }
    for engine in ENGINES:
        rows = [r for c in cases if c["engine"] == engine for r in c["requests"]]
        selected = [c for c in cases if c["engine"] == engine]
        summary["per_engine"][engine] = {
            "requests": len(rows),
            "latency_ms": quantiles([r["http_ms"] for r in rows]),
            "response_bytes": sum(r["bytes"] for r in rows),
            "http_batch_requests_per_second": len(rows)
            / (sum(s["receipt_ms"] for c in selected for s in c["steps"]) / 1000),
            "loaded_latency_ms": quantiles(
                [
                    r["http_ms"]
                    for c in selected
                    if c["users"] > 1
                    for r in c["requests"]
                ]
            ),
            "median_peak_pss_mib": statistics.median(
                c["resources_peak"]["pss_bytes"] / MIB for c in selected
            ),
            "median_peak_uss_mib": statistics.median(
                c["resources_peak"]["uss_bytes"] / MIB for c in selected
            ),
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "| Storage | Layer | Action | RCFP ms | Terracotta ms | TiTiler ms | TC / RCFP | TiTiler / RCFP |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in groups:
        times = group["median_viewport_ms"]
        ratios = group["paired_speedup"]
        lines.append(
            f"| {group['storage']} | {group['mode']} | {group['kind']} | {times['rcfp']:.1f} | {times['terracotta']:.1f} | {times['titiler']:.1f} | {ratios['terracotta']:.2f}× | {ratios['titiler']:.2f}× |"
        )
    (out / "summary.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--titiler-python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument(
        "--storage", nargs="+", choices=("local", "range"), default=["local", "range"]
    )
    parser.add_argument(
        "--scenes", nargs="+", default=["imagery", "elevation", "classes"]
    )
    parser.add_argument("--users", nargs="+", type=int, default=[1, 3])
    parser.add_argument("--latency-ms", type=float, default=15)
    parser.add_argument("--navigation-only", action="store_true")
    parser.add_argument(
        "--rcfp-source-ttl",
        type=int,
        default=60,
        help="Native source lease in seconds; use 0 to reproduce the earlier benchmark",
    )
    args = parser.parse_args()
    if not 0 <= args.rcfp_source_ttl <= 86400:
        parser.error("Source TTL must be between 0 and 86400 seconds")
    if args.trials < 1 or min(args.users) < 1:
        parser.error("Trial/user counts must be positive")
    for field in ("binary", "out"):
        setattr(args, field, getattr(args, field).resolve())
    # Resolve the containing path without following the venv interpreter link.
    args.reference_python = args.reference_python.absolute()
    args.titiler_python = args.titiler_python.absolute()
    args.out.mkdir(parents=True, exist_ok=False)
    args.cpus, args.client_cpus = cpu_allocation()
    if args.client_cpus:
        os.sched_setaffinity(0, args.client_cpus)
    manifest_path = ROOT / "tests/fixtures/navigation.json"
    manifest = json.loads(manifest_path.read_text())
    scenes = [dict(s) for s in manifest["scenes"] if s["id"] in args.scenes]
    for scene in scenes:
        if scene["id"] == "imagery":
            scene["bands"] = {**scene["bands"], "nir": "s2-nir"}
    sources = {
        key: ROOT / ".cache/corpus" / row["file"]
        for key, row in manifest["sources"].items()
        if any(key in s["bands"].values() for s in scenes)
    }
    for key, path in sources.items():
        if digest(path) != manifest["sources"][key]["sha256"]:
            raise ValueError(f"Source checksum mismatch: {key}")
    report = {
        "binary_sha256": digest(args.binary),
        "manifest_sha256": digest(manifest_path),
        "sources": {k: manifest["sources"][k] for k in sources},
        "platform": platform.platform(),
        "native_gdal_library_sha256": digest(
            Path(os.environ["TERRACOTTA_GDAL_LIBRARY"])
        ),
        "native_version": subprocess.check_output(
            [str(args.binary), "--version"], text=True
        ).strip(),
        "benchmark_sha256": digest(Path(__file__)),
        "titiler_adapter_sha256": digest(ROOT / "tests/benchmark_titiler_app.py"),
        "cpu_affinity": args.cpus,
        "client_cpu_affinity": args.client_cpus,
        "trials": args.trials,
        "rcfp_source_ttl_seconds": args.rcfp_source_ttl,
        "cases": [],
        "terracotta": environment(
            args.reference_python, ["terracotta", "rasterio", "gunicorn", "numpy"]
        ),
        "titiler": environment(
            args.titiler_python,
            ["titiler.core", "rio-tiler", "rasterio", "uvicorn", "numpy"],
        ),
        "conditions": {
            "logical_cache_mib": 160,
            "cache_allocations": {
                "rcfp": {"gdal": 64, "arrays": 64, "curl": 32},
                "terracotta": {
                    "processes": 4,
                    "gdal_each": 16,
                    "arrays_each": 16,
                    "curl_each": 8,
                },
                "titiler": {"processes": 4, "gdal_each": 32, "curl_each": 8},
            },
            "rendered_cache": "off",
            "remote_dataset_leases": {
                "rcfp_seconds": args.rcfp_source_ttl,
                "terracotta_seconds": 0,
                "titiler_seconds": 0,
            },
            "png": "level 1 / explicit PNG",
            "continuous_resampling": "bilinear warp/read; different intermediate grid policies",
            "categorical_resampling": "nearest",
            "source_request_latency_ms": args.latency_ms,
            "process_state": "fresh per case, warm OS page cache; no raster prewarming",
            "render_stage": "HTTP completion separate from client decode/record",
            "titiler_concurrency": "four processes; default FastAPI threadpool and rio-tiler asset tasks; all four health PIDs ready before timing",
            "memory": "100 ms process-tree samples; summed PSS and USS; RSS sum diagnostic only",
        },
        "exclusions": [
            "physical cold disk",
            "browser paint / client WAN",
            "real S3 in this comparison",
            "true temporal acquisitions",
            "mosaics (no common dynamic mosaic endpoint)",
            "deadline/disconnect drain (separate request lifecycle correctness gate)",
            "portable release qualification",
        ],
    }
    if report["terracotta"]["gdal"] != report["titiler"]["gdal"]:
        raise ValueError("Reference GDAL versions differ")
    metadata = {
        key: native(
            args.binary,
            args.out,
            os.environ,
            {
                "operation": "compute_metadata",
                "path": str(path),
                "options": {"max_shape": [512, 512]},
            },
        )
        for key, path in sources.items()
    }
    report["metadata"] = metadata

    def save():
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    save()
    for trial in range(args.trials):
        order = ENGINES[trial % 3 :] + ENGINES[: trial % 3]
        for scene in scenes:
            for mode in (
                ["singleband", "rgb"] if "green" in scene["bands"] else ["singleband"]
            ):
                for storage in args.storage:
                    for users in args.users:
                        for overlap in [True, False] if users > 1 else [True]:
                            for engine in order:
                                report["cases"].append(
                                    run_case(
                                        args,
                                        engine,
                                        scene,
                                        mode,
                                        storage,
                                        users,
                                        overlap,
                                        trial,
                                        sources,
                                        metadata,
                                    )
                                )
                                save()
    summarize(report, args.out)
    if any(s["failures"] for c in report["cases"] for s in c["steps"]):
        raise SystemExit("Failed requests are retained; speedup claims require review")


if __name__ == "__main__":
    main()
