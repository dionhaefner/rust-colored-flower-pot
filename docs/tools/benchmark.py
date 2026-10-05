"""Compare local sample previews with installed Terracotta 0.10.1 and RCFP."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import socket
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import rcfp
from PIL import Image

DOCS = Path(__file__).resolve().parents[1]


def image_hash(body):
    return hashlib.sha256(
        Image.open(io.BytesIO(body)).convert("RGBA").tobytes()
    ).hexdigest()


def measure(url):
    start = time.perf_counter()
    with urlopen(url, timeout=30) as response:
        assert response.status == 200
        body = response.read()
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed, image_hash(body)


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * fraction + 0.5))]


def service(engine, args, database, cpus, logfile):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        bind = probe.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TC_", "RCFP_COMPAT_", "TERRACOTTA_"))
    }
    env.update(
        TC_DRIVER_PATH=str(database),
        TC_USE_MULTIPROCESSING="false",
        TC_SOURCE_BLOCK_CACHE_BYTES="0",
    )
    if engine == "rcfp":
        env.update(
            RCFP_COMPAT_PROFILE="native",
            GDAL_CACHEMAX="48",
            TC_RASTER_WORKERS="3",
            TC_RASTER_QUEUE_CAPACITY="32",
            TC_RASTER_CACHE_BYTES=str(48 * 1024**2),
        )
        command = [
            sys.executable,
            "-m",
            "rcfp_cli",
            "serve",
            "-d",
            str(database),
            "--bind",
            f"127.0.0.1:{bind}",
        ]
    else:
        env.update(GDAL_CACHEMAX="16", TC_RASTER_CACHE_SIZE=str(16 * 1024**2))
        command = [
            str(args.reference_python.absolute()),
            "-m",
            "gunicorn",
            "--bind",
            f"127.0.0.1:{bind}",
            "--workers",
            "3",
            "terracotta.server:create_app()",
        ]
    kwargs = {}
    if cpus:
        kwargs["preexec_fn"] = lambda: os.sched_setaffinity(0, cpus)
    process = subprocess.Popen(
        command, stdout=logfile, stderr=logfile, env=env, **kwargs
    )
    origin = f"http://127.0.0.1:{bind}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(origin + "/keys", timeout=1):
                return process, origin
        except (URLError, TimeoutError):
            if process.poll() is not None:
                raise RuntimeError(f"{engine} failed to start; see its log")
            time.sleep(0.05)
    process.terminate()
    process.wait(timeout=30)
    raise TimeoutError(f"{engine} did not start")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--requests", type=int, default=30)
    args = parser.parse_args()
    if args.trials < 3 or args.requests < 10:
        parser.error("Use at least three trials and ten requests per workload")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    ref = subprocess.run(
        [
            str(args.reference_python.absolute()),
            "-c",
            "import json,terracotta,rasterio; from importlib.metadata import version; print(json.dumps({'version':terracotta.__version__, 'gdal':rasterio.__gdal_version__, 'gunicorn':version('gunicorn')}))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    upstream = json.loads(ref.stdout)
    if upstream["version"] != "0.10.1":
        raise RuntimeError("This comparison requires Terracotta 0.10.1")
    spec = importlib.util.spec_from_file_location(
        "create_catalog", DOCS / "examples/create_catalog.py"
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    database = args.out / "catalog.sqlite"
    example.create_catalog(database)
    cpus = (
        sorted(os.sched_getaffinity(0))[:3] if hasattr(os, "sched_getaffinity") else []
    )
    routes = {
        "singleband": "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D",
        "rgb": "/rgb/lagoon/preview.png?r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D",
    }
    expected = {
        name: image_hash(rcfp.request(database, "GET", route)[2])
        for name, route in routes.items()
    }
    report = {
        "rcfp_version": version("rcfp"),
        "terracotta": upstream,
        "native_identity": rcfp.identity(database),
        "platform": platform.platform(),
        "native_sha256": hashlib.sha256(
            (rcfp._RUNTIME_ROOT / "bin/rcfp").read_bytes()
        ).hexdigest(),
        "data": json.loads((DOCS / "examples/data/manifest.json").read_text()),
        "conditions": {
            "cpu_affinity": cpus,
            "trials": args.trials,
            "requests_per_cell": args.requests,
            "concurrency": [1, 2],
            "cache_budget_mib": {
                "rcfp_gdal": 48,
                "rcfp_arrays": 48,
                "python_gdal_per_worker": 16,
                "python_arrays_per_worker": 16,
                "python_workers": 3,
                "total_per_service": 96,
            },
            "fast_paths": "disabled",
            "source_access": "local samples; warm filesystem; first image measured separately",
            "timed_stage": "HTTP response receipt before decoded-image verification",
            "memory_measured": False,
        },
        "results": [],
    }
    for trial in range(args.trials):
        for engine in (
            ["rcfp", "terracotta"] if trial % 2 == 0 else ["terracotta", "rcfp"]
        ):
            with (args.out / f"{engine}-{trial}.log").open("w") as logfile:
                process, origin = service(engine, args, database, cpus, logfile)
                try:
                    if engine == "rcfp":
                        native_gdal = re.search(
                            r"GDAL ([0-9.]+)", Path(logfile.name).read_text()
                        )
                        if not native_gdal or native_gdal.group(1) != upstream["gdal"]:
                            raise RuntimeError(
                                "Use matching GDAL versions for this comparison"
                            )
                        report["native_gdal"] = native_gdal.group(1)
                    cold_ms, cold_hash = measure(origin + routes["singleband"])
                    assert cold_hash == expected["singleband"], (
                        "First-view pixels differ"
                    )
                    for name, route in routes.items():
                        for _ in range(9):
                            _elapsed, digest = measure(origin + route)
                            assert digest == expected[name], "Warmup pixels differ"
                        for concurrency in (1, 2):
                            start = time.perf_counter()
                            with ThreadPoolExecutor(concurrency) as clients:
                                results = list(
                                    clients.map(
                                        lambda _, url=origin + route: measure(url),
                                        range(args.requests),
                                    )
                                )
                            duration = time.perf_counter() - start
                            assert all(
                                digest == expected[name] for _, digest in results
                            ), "Decoded pixels differ"
                            times = [elapsed for elapsed, _ in results]
                            report["results"].append(
                                {
                                    "engine": engine,
                                    "trial": trial,
                                    "mode": name,
                                    "concurrency": concurrency,
                                    "first_singleband_ms": cold_ms,
                                    "p50_ms": statistics.median(times),
                                    "p95_ms": percentile(times, 0.95),
                                    "request_ms": times,
                                    "elapsed_seconds_including_client_checks": duration,
                                    "failures": 0,
                                    "decoded_sha256": expected[name],
                                }
                            )
                finally:
                    process.terminate()
                    process.wait(timeout=30)
    report["passed"] = True
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        "| Workload | Concurrency | RCFP p50 (ms) | Terracotta p50 (ms) | Ratio |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name in routes:
        for concurrency in (1, 2):
            medians = {
                engine: statistics.median(
                    row["p50_ms"]
                    for row in report["results"]
                    if row["engine"] == engine
                    and row["mode"] == name
                    and row["concurrency"] == concurrency
                )
                for engine in ("rcfp", "terracotta")
            }
            lines.append(
                f"| {name} preview | {concurrency} | {medians['rcfp']:.2f} | {medians['terracotta']:.2f} | {medians['terracotta'] / medians['rcfp']:.2f}× |"
            )
    (args.out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
