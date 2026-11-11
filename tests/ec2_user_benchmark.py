"""Compare fixed-schedule multi-user Leaflet sessions on one disposable EC2 host."""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import runpy
import shlex
import shutil
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from lambda_map_benchmark import QuietHandler
from map_sessions import ROOT, SCENES, classify_requests, quantile, run_case

REMOTE = "/opt/rcfp/comparison/remote.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--users", nargs="+", type=int, default=[1, 4, 8])
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--scene", choices=tuple(SCENES), default="sentinel")
    parser.add_argument(
        "--arrangement", choices=("overlapping", "disjoint"), default="overlapping"
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("singleband", "rgb"),
        default=["singleband", "rgb"],
    )
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=("native", "terracotta", "terracotta-tuned"),
        default=["native", "terracotta", "terracotta-tuned"],
    )
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--native-queue-capacity", type=int, default=64)
    parser.add_argument("--continue-on-case-error", action="store_true")
    parser.add_argument(
        "--progress-uri", help="optional S3 object updated between measured cases"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if any(not 1 <= n <= 16 for n in args.users):
        parser.error("user counts must be between 1 and 16")
    if args.trials < 1:
        parser.error("trials must be positive")
    if not 1 <= args.native_queue_capacity <= 1024:
        parser.error("native queue capacity must be between 1 and 1024")
    if args.scene != "sentinel" and "rgb" in args.modes:
        parser.error("elevation and land-cover scenes require --modes singleband")
    args.out.mkdir(parents=True, exist_ok=False)
    session = json.loads(args.session.read_text())
    deployment = json.loads(args.deployment.read_text())
    AWS = runpy.run_path(str(ROOT / "packaging/cloud-test/host.py"))["AWS"]
    client = AWS(session["region"])
    web = args.out / "web"
    web.mkdir()
    shutil.copy2(ROOT / "tests/benchmarks/map-session.html", web / "index.html")
    for name in ("leaflet.js", "leaflet.css"):
        shutil.copy2(ROOT / "src/assets/client/vendor" / name, web / name)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(web.resolve()))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local = f"http://127.0.0.1:{server.server_port}/"
    origin = session["ApiUrl"].replace("http://", "https://")
    spec = json.loads(args.spec.read_text()) if args.spec else None
    report = {
        "passed": False,
        "session": session,
        "deployment": deployment,
        "arms": args.arms,
        "users": args.users,
        "modes": args.modes,
        "trials": args.trials,
        "scene": args.scene,
        "arrangement": args.arrangement,
        "cases": [],
        "scope": "Fixed-schedule animated Leaflet view changes in independent browser contexts; one EC2 host, two workers, CPU IDs 0,1 and 2 GiB per service. HTTP/1.1 with six connections per user. Initial map readiness is separate from paced navigation and rapid bursts. No compositor presentation timestamps.",
        "coverage": "50 ms samples of viewport area covered by decoded successful tiles at the requested zoom and full tile opacity. Legitimate transparent tiles count as loaded. Completion requires two ready samples and no active map animation. Rapid targets may supersede unfinished targets.",
        "tuned_terracotta": "Supported RASTER_CACHE_COMPRESS_LEVEL=1; original uses 9. Other settings and package image unchanged.",
        "native_queue_capacity": args.native_queue_capacity,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "tests/ec2_user_benchmark.py",
                "tests/map_sessions.py",
                "tests/benchmarks/map-session.html",
                "packaging/ec2-comparison/remote.py",
            )
        },
    }
    try:
        for trial in range(args.trials):
            users_order = args.users if trial % 2 == 0 else args.users[::-1]
            for users in users_order:
                for mode_index, mode in enumerate(args.modes):
                    shift = (trial + mode_index) % len(args.arms)
                    arms = args.arms[shift:] + args.arms[:shift]
                    for arm in arms:
                        name = f"{args.out.name}-t{trial}-u{users}-{mode}-{arm}"
                        folder = args.out / name
                        folder.mkdir()
                        metadata = {
                            "case": name,
                            "trial": trial,
                            "users": users,
                            "mode": mode,
                            "arm": arm,
                            "passed": False,
                        }
                        report["cases"].append(metadata)
                        command = [
                            "python3",
                            REMOTE,
                            "start",
                            "--engine",
                            "native" if arm == "native" else "terracotta",
                            "--case",
                            name,
                        ]
                        command += [
                            "--resampling-method",
                            "nearest" if args.scene == "worldcover" else "linear",
                        ]
                        if arm == "terracotta-tuned":
                            command += ["--cache-compression-level", "1"]
                        if arm == "native":
                            command += [
                                "--queue-capacity",
                                str(args.native_queue_capacity),
                            ]
                        try:
                            client.command(
                                session["InstanceId"],
                                shlex.join(command),
                                folder / "start.json",
                            )
                            before = {
                                "epoch_s": time.time(),
                                "meminfo": Path("/proc/meminfo").read_text(),
                                "stat": Path("/proc/stat").read_text(),
                            }
                            result = asyncio.run(
                                run_case(
                                    origin,
                                    local,
                                    users,
                                    mode,
                                    folder,
                                    spec=spec,
                                    scene=args.scene,
                                    arrangement=args.arrangement,
                                )
                            )
                            result["client_before"] = before
                            result["client_after"] = {
                                "epoch_s": time.time(),
                                "meminfo": Path("/proc/meminfo").read_text(),
                                "stat": Path("/proc/stat").read_text(),
                            }
                            measured_requests = classify_requests(
                                result["requests"], result["sessions"]
                            )
                            (folder / "result.json").write_text(
                                json.dumps(result, indent=2) + "\n"
                            )
                            metadata.update(
                                passed=not result["javascript_errors"],
                                summary=result["summary"],
                                measured_requests=len(measured_requests),
                                http_errors=sum(
                                    r.get("status", 0) >= 400 for r in measured_requests
                                ),
                                canceled_requests=sum(
                                    r.get("canceled", False)
                                    and not r.get("canceled_after_session", False)
                                    for r in measured_requests
                                ),
                                cleanup_cancellations=sum(
                                    r.get("canceled_after_session", False)
                                    for r in measured_requests
                                ),
                                network_errors=sum(
                                    "network_error" in r
                                    and not r.get("canceled", False)
                                    for r in measured_requests
                                ),
                                dispatch_lateness_p95_ms=sorted(
                                    a["dispatch_lateness_ms"]
                                    for s in result["sessions"]
                                    for a in s["actions"]
                                )[
                                    int(
                                        0.95
                                        * sum(
                                            len(s["actions"])
                                            for s in result["sessions"]
                                        )
                                    )
                                ],
                            )
                            metadata["sample_gap_p95_ms"] = quantile(
                                [
                                    b["at_ms"] - a["at_ms"]
                                    for s in result["sessions"]
                                    for a, b in zip(s["samples"], s["samples"][1:])
                                ],
                                0.95,
                            )
                            metadata["client_schedule_valid"] = (
                                metadata["dispatch_lateness_p95_ms"] <= 100
                                and metadata["sample_gap_p95_ms"] <= 150
                            )
                        except Exception as exc:
                            metadata["error"] = {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            }
                            if not args.continue_on_case_error:
                                raise
                        finally:
                            client.command(
                                session["InstanceId"],
                                f"python3 {REMOTE} stop",
                                folder / "stop.json",
                            )
                            (args.out / "report.json").write_text(
                                json.dumps(report, indent=2) + "\n"
                            )
                            if args.progress_uri:
                                client.call(
                                    "s3",
                                    "cp",
                                    str(args.out / "report.json"),
                                    args.progress_uri,
                                    "--only-show-errors",
                                )
                        print(json.dumps(metadata), flush=True)
        report["passed"] = all(c["passed"] for c in report["cases"])
    finally:
        server.shutdown()
        server.server_close()
        try:
            client.command(
                session["InstanceId"],
                f"python3 {REMOTE} collect",
                args.out / "collection.json",
                timeout=300,
            )
            client.call(
                "s3",
                "sync",
                f"s3://{session['bucket']}/runs/{session['stack']}/comparison/",
                str(args.out / "server"),
                "--only-show-errors",
            )
        except Exception as exc:
            report["collection_error"] = str(exc)
            report["passed"] = False
            raise
        finally:
            (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            if args.progress_uri:
                client.call(
                    "s3",
                    "cp",
                    str(args.out / "report.json"),
                    args.progress_uri,
                    "--only-show-errors",
                )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
