"""Run matched container servers and collect separate EC2 profiling evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import boto3

BASE = Path("/opt/rcfp/comparison")
CONTAINER = "rcfp-comparison"


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def configuration():
    return json.loads((BASE / "configuration.json").read_text())


def clients():
    session = boto3.Session(region_name=configuration()["region"])
    return session.client("s3"), session.client("ecr")


def setup():
    cfg = configuration()
    s3, ecr = clients()
    token = ecr.get_authorization_token()["authorizationData"][0]
    user, password = (
        base64.b64decode(token["authorizationToken"]).decode().split(":", 1)
    )
    subprocess.run(
        [
            "docker",
            "login",
            "--username",
            user,
            "--password-stdin",
            token["proxyEndpoint"],
        ],
        input=password,
        text=True,
        check=True,
    )
    for image in cfg["images"].values():
        subprocess.run(["docker", "pull", image], check=True)
    (BASE / "tools").mkdir(exist_ok=True)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "--entrypoint",
            "python",
            "-v",
            f"{BASE}/tools:/tools",
            cfg["images"]["terracotta"],
            "-m",
            "pip",
            "install",
            "--target",
            "/tools",
            "gunicorn==23.0.0",
            "packaging==26.3",
        ],
        check=True,
    )
    (BASE / "data").mkdir(exist_ok=True)
    manifest = cfg["manifest"]
    with (
        sqlite3.connect(BASE / "catalog.sqlite") as src,
        sqlite3.connect(BASE / "catalog-local.sqlite") as dst,
    ):
        src.backup(dst)
        for row in manifest["sources"]:
            if row["scene"] == "lagoon":
                continue
            path = BASE / "data" / Path(row["key"]).name
            s3.download_file(manifest["bucket"], row["key"], str(path))
            with path.open("rb") as stream:
                assert (
                    hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"]
                )
            dst.execute(
                "UPDATE datasets SET path=? WHERE scene=? AND band=?",
                (f"/data/{path.name}", row["scene"], row["band"]),
            )
    save(
        BASE / "host.json",
        {
            "lscpu": json.loads(run("lscpu", "--json")),
            "kernel": run("uname", "-a"),
            "docker": run("docker", "version", "--format", "{{json .Server}}"),
            "perf": run("perf", "version"),
        },
    )
    print("comparison setup complete")


def nginx(folder):
    config = f"""
events {{ worker_connections 512; }}
http {{
  log_format timing escape=json '{{"id":"$request_id","path":"$request_uri","status":$status,"time":$msec,"total_s":$request_time,"upstream_s":"$upstream_response_time","connect_s":"$upstream_connect_time","header_s":"$upstream_header_time","bytes":$body_bytes_sent}}';
  access_log {folder}/nginx.jsonl timing;
  error_log {folder}/nginx-errors.log;
  server {{
    listen 5000 ssl;
    ssl_certificate {BASE}/certificate.pem;
    ssl_certificate_key {BASE}/key.pem;
    ssl_session_cache shared:TLS:1m;
    keepalive_timeout 65;
    add_header X-Benchmark-Request-Id $request_id always;
    location / {{
      proxy_pass http://127.0.0.1:5001;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_buffering off;
      proxy_read_timeout 90s;
    }}
  }}
}}
"""
    (BASE / "nginx.conf").write_text(config)
    subprocess.run(
        ["nginx", "-c", str(BASE / "nginx.conf"), "-p", str(BASE)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def snapshot(folder, name):
    pid = int(run("docker", "inspect", "--format", "{{.State.Pid}}", CONTAINER))
    lines = run("docker", "top", CONTAINER, "-eo", "pid,args").splitlines()[1:]
    pids = [int(line.split()[0]) for line in lines]
    stats = {}
    for selected in pids:
        root = Path(f"/proc/{selected}")
        if not root.exists():
            continue
        stats[str(selected)] = {
            "stat": (root / "stat").read_text(),
            "io": (root / "io").read_text(),
            "smaps_rollup": (root / "smaps_rollup").read_text(),
        }
    cgroup = Path("/sys/fs/cgroup") / (
        Path(f"/proc/{pid}/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    )
    save(
        folder / name,
        {
            "processes": stats,
            "host_stat": Path("/proc/stat").read_text(),
            "cpu_stat": (cgroup / "cpu.stat").read_text(),
            "time": time.time(),
            "clock_ticks": os.sysconf("SC_CLK_TCK"),
        },
    )


def start(args):
    stop()
    cfg = configuration()
    folder = BASE / "runs" / args.case
    folder.mkdir(parents=True, exist_ok=False)
    profiles = folder / "profiles"
    profiles.mkdir()
    database = "catalog-local.sqlite" if args.source == "local" else "catalog.sqlite"
    env = {
        "TC_DRIVER_PATH": "/catalog.sqlite",
        "AWS_REGION": cfg["region"],
        "AWS_DEFAULT_REGION": cfg["region"],
        "TC_USE_MULTIPROCESSING": "false",
        "TC_PNG_COMPRESS_LEVEL": "1",
        "TC_RENDER_CACHE_BYTES": "0",
        "TC_RASTER_WORKERS": "2",
        "TC_RASTER_QUEUE_CAPACITY": str(args.queue_capacity),
        "TC_RASTER_TIMEOUT_MS": "60000",
        "TC_RASTER_CACHE_BYTES": str(64 * 1024**2),
        "TC_REMOTE_RASTER_CACHE_TTL_SECONDS": "60",
        "TC_REMOTE_SOURCE_CACHE_TTL_SECONDS": "60",
        "TC_SOURCE_BLOCK_CACHE_BYTES": "0",
        "TC_PREFETCH_TILE_RING": "0",
        "TC_EXPOSE_METRICS": "true",
        "GDAL_NUM_THREADS": "1",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "VSI_CACHE": "FALSE",
        "CPL_VSIL_CURL_CHUNK_SIZE": "16384",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "PYTHONPATH": "/tools:/adapter",
        "PYTHONHASHSEED": "0",
    }
    if args.resampling_method:
        env.update(
            TC_RESAMPLING_METHOD=args.resampling_method,
            TC_REPROJECTION_METHOD=args.resampling_method,
        )
    if args.engine == "native":
        env.update(
            TC_COMPAT_PROFILE="native",
            GDAL_CACHEMAX="64",
            CPL_VSIL_CURL_CACHE_SIZE=str(32 * 1024**2),
            TC_GDAL_OPTIONS=json.dumps(
                {
                    "CPL_VSIL_CURL_CACHE_SIZE": str(32 * 1024**2),
                    "CPL_VSIL_CURL_CHUNK_SIZE": "16384",
                }
            ),
            TERRACOTTA_RUNTIME_ROOT="/var/runtime/runtime",
            LD_LIBRARY_PATH="/var/runtime/runtime/lib",
            GDAL_DATA="/var/runtime/runtime/share/gdal",
            PROJ_DATA="/var/runtime/runtime/share/proj",
        )
        if args.profile:
            env["TC_PROFILE_RASTER"] = "1"
        command = [
            "/var/runtime/runtime/bin/rcfp",
            "serve",
            "-d",
            "/catalog.sqlite",
            "--bind",
            "127.0.0.1:5001",
        ]
    else:
        env.update(
            GDAL_CACHEMAX="32",
            TC_RASTER_CACHE_SIZE=str(32 * 1024**2),
            TC_RASTER_CACHE_COMPRESS_LEVEL=str(args.cache_compression_level),
            CPL_VSIL_CURL_CACHE_SIZE=str(16 * 1024**2),
        )
        command = [
            "python",
            "-m",
            "gunicorn",
            "--workers",
            "2",
            "--preload",
            "--timeout",
            "90",
            "--bind",
            "127.0.0.1:5001",
            "--access-logfile",
            "-",
            "--access-logformat",
            "%(p)s %(D)s %(U)s",
            "profile_app:app" if args.profile else "terracotta.server:create_app()",
        ]
    docker = [
        "docker",
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--network",
        "host",
        "--cpuset-cpus",
        "0,1",
        "--memory",
        "2g",
        "--memory-swap",
        "2g",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=256m",
        "--cap-drop",
        "ALL",
        "-v",
        f"{BASE / database}:/catalog.sqlite:ro",
        "-v",
        f"{BASE}/data:/data:ro",
        "-v",
        f"{BASE}/tools:/tools:ro",
        "-v",
        f"{BASE}:/adapter:ro",
        "-v",
        f"{profiles}:/profiles",
    ]
    for key, value in env.items():
        docker.extend(["-e", f"{key}={value}"])
    docker.extend(
        ["--entrypoint", command[0], cfg["images"][args.engine], *command[1:]]
    )
    run(*docker)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:5001/keys", timeout=1) as response:
                assert response.status == 200
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError(run("docker", "logs", CONTAINER))
    # Gunicorn preloads the application before forking both workers.
    if args.engine == "terracotta":
        while time.monotonic() < deadline:
            if (
                len(run("docker", "top", CONTAINER, "-eo", "pid,args").splitlines())
                >= 4
            ):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("both Gunicorn workers did not start")
    nginx(folder)
    save(
        BASE / "active.json",
        {
            "case": args.case,
            "engine": args.engine,
            "source": args.source,
            "profile": args.profile,
            "environment": env,
            "started_at": time.time(),
        },
    )
    snapshot(folder, "before.json")
    if args.profile or args.perf_only:
        pids = ",".join(
            line.split()[0]
            for line in run("docker", "top", CONTAINER, "-eo", "pid,args").splitlines()[
                1:
            ]
        )
        with (folder / "perf.log").open("w") as log:
            perf = subprocess.Popen(
                [
                    "perf",
                    "record",
                    "-e",
                    "cpu-clock",
                    "-F",
                    "99",
                    "-g",
                    "--call-graph",
                    "dwarf,8192",
                    "-p",
                    pids,
                    "-o",
                    str(folder / "perf.data"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        save(folder / "perf-process.json", {"pid": perf.pid})
        time.sleep(0.2)
    print(
        json.dumps(
            {
                "case": args.case,
                "engine": args.engine,
                "source": args.source,
                "profile": args.profile,
            }
        )
    )


def stop():
    active = BASE / "active.json"
    if not active.exists():
        return
    state = json.loads(active.read_text())
    folder = BASE / "runs" / state["case"]
    snapshot(folder, "after.json")
    if state["engine"] == "native":
        with urlopen(
            "http://127.0.0.1:5001/__terracotta/metrics", timeout=10
        ) as response:
            (folder / "metrics.json").write_bytes(response.read())
    perf = folder / "perf-process.json"
    if perf.exists():
        try:
            os.kill(json.loads(perf.read_text())["pid"], signal.SIGINT)
        except ProcessLookupError:
            pass
        time.sleep(1)
        result = subprocess.run(
            [
                "perf",
                "report",
                "--stdio",
                "--no-children",
                "--percent-limit",
                "0",
                "--sort",
                "dso,symbol",
                "--symfs",
                f"/proc/{run('docker', 'inspect', '--format', '{{.State.Pid}}', CONTAINER)}/root",
                "-i",
                str(folder / "perf.data"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        (folder / "perf-report.txt").write_text(result.stdout + result.stderr)
    result = subprocess.run(
        ["docker", "logs", CONTAINER], capture_output=True, text=True, check=False
    )
    (folder / "server.log").write_text(result.stdout + result.stderr)
    save(folder / "configuration.json", state)
    subprocess.run(
        ["nginx", "-c", str(BASE / "nginx.conf"), "-p", str(BASE), "-s", "quit"],
        check=True,
    )
    subprocess.run(["docker", "rm", "-f", CONTAINER], check=True)
    active.unlink()


def collect():
    stop()
    cfg = configuration()
    s3, _ = clients()
    for path in BASE.rglob("*"):
        if path.is_file() and (
            path.is_relative_to(BASE / "runs") or path.name == "host.json"
        ):
            s3.upload_file(
                str(path),
                cfg["bucket"],
                cfg["result_prefix"] + str(path.relative_to(BASE)),
            )
    print(json.dumps({"bucket": cfg["bucket"], "prefix": cfg["result_prefix"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("setup", "stop", "collect"):
        commands.add_parser(name)
    start_parser = commands.add_parser("start")
    start_parser.add_argument(
        "--engine", choices=("native", "terracotta"), required=True
    )
    start_parser.add_argument("--source", choices=("s3", "local"), default="s3")
    start_parser.add_argument("--case", required=True)
    start_parser.add_argument(
        "--queue-capacity", type=int, choices=range(1, 1025), default=64
    )
    start_parser.add_argument("--resampling-method", choices=("linear", "nearest"))
    start_parser.add_argument(
        "--cache-compression-level", type=int, choices=(0, 1, 9), default=9
    )
    profiling = start_parser.add_mutually_exclusive_group()
    profiling.add_argument("--profile", action="store_true")
    profiling.add_argument("--perf-only", action="store_true")
    args = parser.parse_args()
    if args.action == "start":
        start(args)
    else:
        globals()[args.action]()


if __name__ == "__main__":
    main()
