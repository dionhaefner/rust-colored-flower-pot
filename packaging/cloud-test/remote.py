"""Install and collect a native test deployment through the EC2 instance role."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from urllib.request import urlopen

BASE = Path("/opt/rcfp")
STATE = Path("/var/lib/rcfp")


def physical_cpus(workers):
    selected, identities = [], set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        identity = tuple(
            (topology / name).read_text().strip()
            for name in ("physical_package_id", "core_id")
        )
        if identity not in identities:
            selected.append(cpu)
            identities.add(identity)
    if workers > len(selected):
        raise ValueError(f"requested {workers} workers but only {len(selected)} cores")
    return selected[:workers]


def unit(workers, cpus, region, profile):
    return f"""[Unit]
Description=RCFP cloud test server
After=network-online.target
Wants=network-online.target
[Service]
User=rcfp
WorkingDirectory=/var/lib/rcfp
Environment=TERRACOTTA_RUNTIME_ROOT=/opt/rcfp/deployment/runtime
Environment=TERRACOTTA_GDAL_LIBRARY=/opt/rcfp/deployment/runtime/lib/libgdal.so
Environment=LD_LIBRARY_PATH=/opt/rcfp/deployment/runtime/lib
Environment=GDAL_DATA=/opt/rcfp/deployment/runtime/share/gdal
Environment=PROJ_DATA=/opt/rcfp/deployment/runtime/share/proj
Environment=AWS_REGION={region}
Environment=AWS_DEFAULT_REGION={region}
Environment=RCFP_COMPAT_PROFILE={profile}
Environment=TC_RASTER_WORKERS={workers}
Environment=TC_RASTER_QUEUE_CAPACITY=16
Environment=TC_RASTER_CACHE_BYTES=67108864
Environment=TC_RENDER_CACHE_BYTES=0
Environment=TC_EXPOSE_METRICS=true
Environment=TC_PNG_COMPRESS_LEVEL=1
Environment=GDAL_CACHEMAX=64
Environment=GDAL_NUM_THREADS=1
Environment=GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR
Environment=VSI_CACHE=FALSE
Environment='TC_GDAL_OPTIONS={{"CPL_VSIL_CURL_CACHE_SIZE":"33554432","CPL_VSIL_CURL_CHUNK_SIZE":"16384"}}'
CPUAffinity={" ".join(map(str, cpus))}
ExecStart=/opt/rcfp/deployment/runtime/bin/rcfp serve -d /var/lib/rcfp/catalog.sqlite --bind 0.0.0.0:5000
TimeoutStopSec=60
KillMode=control-group
[Install]
WantedBy=multi-user.target
"""


def s3(region):
    import boto3

    return boto3.client("s3", region_name=region)


def install(args):
    cpus = physical_cpus(args.workers)
    archive = BASE / "deployment.tar.gz"
    s3(args.region).download_file(args.bucket, args.key, str(archive))
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != args.sha256:
            raise ValueError("deployment archive checksum mismatch")
    pending = BASE / "pending"
    if pending.exists():
        shutil.rmtree(pending)
    pending.mkdir()
    with tarfile.open(archive) as packed:
        packed.extractall(pending, filter="data")
    if (STATE / "deployment.json").exists():
        collect(True)
    current = BASE / "deployment"
    if current.exists():
        shutil.rmtree(current)
    pending.rename(current)
    shutil.copy2(current / "catalog.sqlite", STATE / "catalog.sqlite")
    shutil.chown(STATE / "catalog.sqlite", user="rcfp", group="rcfp")
    deployment = json.loads((current / "manifest.json").read_text())
    deployment.update(
        bucket=args.bucket,
        prefix=args.prefix,
        region=args.region,
        archive_sha256=args.sha256,
        workers=args.workers,
        cpus=cpus,
        profile=args.profile,
        installed_at=time.time(),
    )
    (STATE / "resources.jsonl").unlink(missing_ok=True)
    if (STATE / "reports").exists():
        shutil.rmtree(STATE / "reports")
    (STATE / "deployment.json").write_text(json.dumps(deployment, indent=2) + "\n")
    (Path("/etc/systemd/system") / "rcfp.service").write_text(
        unit(args.workers, cpus, args.region, args.profile)
    )
    (Path("/etc/systemd/system") / "rcfp-sample.service").write_text(
        "[Unit]\nDescription=Sample RCFP host resources\nAfter=rcfp.service\n"
        "[Service]\nExecStart=/usr/bin/python3 /opt/rcfp/remote.py sample\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "rcfp", "rcfp-sample"], check=True)
    archive.unlink()
    for _ in range(60):
        try:
            with urlopen("http://127.0.0.1:5000/keys", timeout=1) as response:
                json.load(response)
            collect(False)
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError("server did not start; collect the journal before teardown")


def sample():
    while True:
        pid = subprocess.check_output(
            ["systemctl", "show", "rcfp", "--property=MainPID", "--value"], text=True
        ).strip()
        try:
            status = {}
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                key, value = line.split(":", 1)
                if key in ("VmRSS", "VmHWM", "Threads"):
                    status[key] = value.strip()
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            status["cpu_seconds"] = (int(stat[11]) + int(stat[12])) / os.sysconf(
                "SC_CLK_TCK"
            )
            status.update(time=time.time(), pid=int(pid), loadavg=os.getloadavg())
            with (STATE / "resources.jsonl").open("a") as output:
                output.write(json.dumps(status) + "\n")
        except FileNotFoundError:
            pass
        time.sleep(5)


def collect(stop):
    deployment = json.loads((STATE / "deployment.json").read_text())
    report = STATE / "reports"
    report.mkdir(exist_ok=True)
    shutil.copy2(STATE / "deployment.json", report / "deployment.json")
    shutil.copy2("/etc/systemd/system/rcfp.service", report / "server.service")
    host = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "clock_ticks": os.sysconf("SC_CLK_TCK"),
        "collected_at": time.time(),
    }
    try:
        with urlopen(
            "http://127.0.0.1:5000/__terracotta/metrics", timeout=5
        ) as response:
            (report / "metrics.json").write_bytes(response.read())
    except OSError as exc:
        host["metrics_error"] = str(exc)
        (report / "metrics.json").unlink(missing_ok=True)
    if stop:
        subprocess.run(["systemctl", "stop", "rcfp-sample", "rcfp"], check=False)
    for command, name in (
        (["lscpu", "--json"], "cpu.json"),
        (["journalctl", "-u", "rcfp", "--no-pager", "-n", "2000"], "server.log"),
        (["systemctl", "status", "rcfp", "--no-pager"], "service.txt"),
        (["systemctl", "list-timers", "rcfp-expire", "--no-pager"], "expiry.txt"),
    ):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        (report / name).write_text(result.stdout + result.stderr)
    if (STATE / "resources.jsonl").exists():
        shutil.copy2(STATE / "resources.jsonl", report / "resources.jsonl")
    (report / "host.json").write_text(json.dumps(host, indent=2) + "\n")
    client = s3(deployment["region"])
    for path in sorted(report.iterdir()):
        client.upload_file(
            str(path), deployment["bucket"], deployment["prefix"] + path.name
        )
    return {"bucket": deployment["bucket"], "prefix": deployment["prefix"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser("deploy")
    for name in ("bucket", "key", "sha256", "prefix", "region"):
        deploy.add_argument(f"--{name}", required=True)
    deploy.add_argument("--workers", type=int, required=True)
    deploy.add_argument(
        "--profile", choices=("native", "terracotta-0.10.1"), required=True
    )
    gather = commands.add_parser("collect")
    gather.add_argument("--stop", action="store_true")
    commands.add_parser("sample")
    args = parser.parse_args()
    if args.command == "deploy":
        install(args)
    elif args.command == "collect":
        print(json.dumps(collect(args.stop)))
    else:
        sample()


if __name__ == "__main__":
    main()
