"""Run the deployed Leaflet workload against matched servers on one EC2 host."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import runpy
import shlex
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from lambda_map_benchmark import ACTIONS, QuietHandler, map_case
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/opt/rcfp/comparison/remote.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--source", choices=("s3", "local"), default="s3")
    parser.add_argument("--animations", choices=("on", "off"), default="on")
    profiling = parser.add_mutually_exclusive_group()
    profiling.add_argument("--profile", action="store_true")
    profiling.add_argument("--perf-only", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    session = json.loads(args.session.read_text())
    deployment = json.loads(args.deployment.read_text())
    AWS = runpy.run_path(str(ROOT / "packaging/cloud-test/host.py"))["AWS"]
    client = AWS(session["region"])
    web = args.out / "web"
    web.mkdir()
    shutil.copy2(ROOT / "tests/benchmarks/lambda-map.html", web / "index.html")
    for name in ("leaflet.js", "leaflet.css"):
        shutil.copy2(ROOT / "src/assets/client/vendor" / name, web / name)
    handler = functools.partial(QuietHandler, directory=str(web.resolve()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = f"http://127.0.0.1:{server.server_port}/?animations={int(args.animations == 'on')}"
    origin = session["ApiUrl"].replace("http://", "https://")
    records, rows = [], []
    report = {
        "passed": False,
        "deployment": deployment,
        "session": session,
        "rows": rows,
        "trials": args.trials,
        "source": args.source,
        "animations": args.animations,
        "profiled": args.profile,
        "cpu_sampled": args.profile or args.perf_only,
        "viewport": [1600, 1000],
        "actions": ACTIONS,
        "transport": "direct EC2 nginx TLS with a self-signed benchmark certificate accepted by this client; HTTP/1.1",
        "server_limits": {
            "cpuset": "0,1",
            "memory_bytes": 2 * 1024**3,
            "workers": 2,
            "native_cache_mib": {"gdal": 64, "arrays": 64, "ranges": 32},
            "terracotta_per_worker_cache_mib": {"gdal": 32, "arrays": 32, "ranges": 16},
        },
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256((web / "index.html").read_bytes()).hexdigest(),
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            report["browser_version"] = browser.version
            try:
                for trial in range(args.trials):
                    order = (
                        ["native", "terracotta"]
                        if trial % 2 == 0
                        else ["terracotta", "native"]
                    )
                    for mode in ("singleband", "rgb"):
                        for engine in order if mode == "singleband" else order[::-1]:
                            case = f"{args.out.name}-{trial}-{mode}-{engine}"
                            command = [
                                "python3",
                                REMOTE,
                                "start",
                                "--engine",
                                engine,
                                "--source",
                                args.source,
                                "--case",
                                case,
                            ]
                            if args.profile:
                                command.append("--profile")
                            elif args.perf_only:
                                command.append("--perf-only")
                            client.command(
                                session["InstanceId"],
                                shlex.join(command),
                                args.out / f"{case}-start.json",
                            )
                            (args.out / "images" / engine).mkdir(
                                parents=True, exist_ok=True
                            )
                            rows.extend(
                                map_case(
                                    browser,
                                    origin,
                                    engine,
                                    mode,
                                    trial,
                                    local,
                                    args.out,
                                    records,
                                    ignore_https_errors=True,
                                )
                            )
                            client.command(
                                session["InstanceId"],
                                f"python3 {REMOTE} stop",
                                args.out / f"{case}-stop.json",
                            )
                            (args.out / "progress.json").write_text(
                                json.dumps(
                                    {
                                        "completed_maps": len(rows) // len(ACTIONS),
                                        "errors": sum(r["errors"] for r in rows),
                                    }
                                )
                                + "\n"
                            )
            finally:
                browser.close()
        report["passed"] = len(rows) == args.trials * 4 * len(ACTIONS) and all(
            not r.get("error") and r["errors"] == 0 and not r["javascript_errors"]
            for r in rows
        )
    except BaseException as exc:
        report["run_error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        server.shutdown()
        server.server_close()
        collection_error = None
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
        except Exception as exc:  # noqa: BLE001 -- retain cloud collection failure beside request outcomes
            collection_error = str(exc)
            report["passed"] = False
        report.update(
            collection_error=collection_error,
            request_count=len(records),
            request_errors=sum(r["status"] != 200 or "error" in r for r in records),
        )
        (args.out / "requests.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records)
        )
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "passed",
                    "request_count",
                    "request_errors",
                    "collection_error",
                )
            },
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
