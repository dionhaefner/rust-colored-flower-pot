"""Compare foreground latency with isolated coalescing and queued-expiry controls.

Both arms serve checksum-pinned real raster pixels with identical budgets.
Control binaries differ only by disabling the named mechanism. Pixel comparisons
check invariance between arms. Independent rendering gates remain separate.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import threading
import time
from pathlib import Path

from request_cost import Native, Origin, RangeHandler, until
from resource_contracts import MANIFEST, RASTER, operation, require, resources


class DelayedRangeHandler(RangeHandler):
    def reply(self, body):
        if self.command == "HEAD" and self.path.startswith("/obsolete-"):
            time.sleep(0.075)
        super().reply(body)


def tile(scene, size=256):
    return (
        f"/singleband/{scene}/preview.png?stretch_range=%5B0%2C3000%5D"
        f"&tile_size=%5B{size}%2C{size}%5D"
    )


def metrics(app):
    status, body = app.request("/__terracotta/metrics")
    require(status == 200, "monitoring request rejected")
    return json.loads(body)


def timed_request(app, path):
    start = time.monotonic()
    status, body = app.request(path)
    return status, body, time.monotonic() - start


def exercise(binary, template, origin, out, workload, candidate):
    out.mkdir()
    database = out / "catalog.sqlite"
    shutil.copyfile(template, database)
    source = f"slow-{out.name}.tif"
    obsolete = [f"obsolete-{out.name}-{i}.tif" for i in range(4)]
    with sqlite3.connect(database) as connection:
        for scene, name in [("slow", source), *zip(range(4), obsolete)]:
            scene = scene if isinstance(scene, str) else f"obsolete-{scene}"
            connection.execute(
                "UPDATE datasets SET path=? WHERE scene=?",
                (f"http://127.0.0.1:{origin.server_port}/{name}", scene),
            )
    settings = {
        "RASTER_WORKERS": 1,
        "RASTER_QUEUE_CAPACITY": 16,
        "RASTER_TIMEOUT_MS": 1000 if workload == "queued_expiry" else 5000,
        "RASTER_CACHE_BYTES": 0,
        "RENDER_CACHE_BYTES": 0,
        "SOURCE_BLOCK_CACHE_BYTES": 0,
        "REMOTE_SOURCE_CACHE_TTL_SECONDS": 0,
        "EXPOSE_METRICS": True,
        "DEBUG": False,
        "LOGLEVEL": "warning",
    }
    app = Native(binary, database, settings, out)
    gate, seen = origin.gate(source)
    for name in obsolete:
        origin.seen[name] = threading.Event()
    peak = {"rss_bytes": 0, "fds": 0}
    stop = threading.Event()

    def sample_resources():
        while not stop.is_set():
            try:
                rss, fds = resources(app.process.pid)
            except OSError:
                return
            peak["rss_bytes"] = max(peak["rss_bytes"], rss)
            peak["fds"] = max(peak["fds"], fds)
            stop.wait(0.01)

    sampler = threading.Thread(target=sample_resources)
    sampler.start()
    try:
        # Warm the shared local source handle equally, without image/array retention.
        require(app.request(tile("view-0"))[0] == 200, "warmup failed")
        before = metrics(app)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            try:
                leader = pool.submit(app.request, tile("slow", 1024))
                require(seen.wait(5), "slow source never began")
                if workload == "queued_expiry":
                    older = [
                        pool.submit(app.request, tile(f"obsolete-{i}"))
                        for i in range(4)
                    ]
                    until(lambda: metrics(app)["queued"] == 4, "old work not queued")
                    require(
                        all(f.result()[0] == 504 for f in [leader, *older]),
                        "old requests did not expire",
                    )
                    expected_queued = 8
                else:
                    duplicates = [
                        pool.submit(app.request, tile("slow", 1024)) for _ in range(5)
                    ]
                    until(
                        lambda: (
                            metrics(app)["render_cache"]["coalesced"] == 5
                            if candidate
                            else metrics(app)["queued"] == 5
                        ),
                        "duplicate workload not admitted",
                    )
                    expected_queued = 4 if candidate else 9
                started = time.monotonic()
                views = [
                    pool.submit(timed_request, app, tile(f"view-{i}")) for i in range(4)
                ]
                until(
                    lambda: metrics(app)["queued"] == expected_queued,
                    "foreground workload not queued",
                )
                gate.set()
                results = [future.result() for future in views]
                viewport = time.monotonic() - started
                require(all(r[0] == 200 for r in results), "foreground render failed")
                hashes = [hashlib.sha256(r[1]).hexdigest() for r in results]
                require(len(set(hashes)) == 1, "viewport pixel results differ")
                if workload == "coalescing":
                    duplicate_results = [f.result() for f in [leader, *duplicates]]
                    require(
                        all(
                            r[0] == 200 and r[1] == duplicate_results[0][1]
                            for r in duplicate_results
                        ),
                        "duplicate render failed or changed bytes",
                    )
            finally:
                gate.set()
        until(
            lambda: metrics(app)["in_flight"] == metrics(app)["queued"] == 0,
            "native work did not drain",
        )
        after = metrics(app)
        require(
            after["submitted"]
            == after["completed"] + after["failed"] + after["cancelled_queued"],
            "lost work accounting",
        )
        require(
            after["failed"]
            == after["overloaded"]
            == after["cost"]["render_overloaded"]
            == after["cost"]["http_overloaded"]
            == 0,
            "bounded workload failed or overloaded",
        )
        require(
            after["workers"] == 1
            and after["queue_capacity"] == 16
            and after["peak_in_flight"] == 1
            and after["prefetch_completed"] == after["prefetch_in_flight"] == 0,
            "foreground worker budget changed",
        )
        require(
            after["render_admitted"] == after["render_cache"]["in_flight"] == 0
            and after["cache_bytes"]
            == after["cache_capacity_bytes"]
            == after["render_cache"]["bytes"]
            == after["render_cache"]["capacity_bytes"]
            == 0,
            "admission or retention did not drain",
        )
        if workload == "queued_expiry":
            require(
                after["cancelled_queued"] == (4 if candidate else 0),
                "queued-expiry control not isolated",
            )
            require(
                all(origin.seen[name].is_set() == (not candidate) for name in obsolete),
                "unexpected obsolete source access",
            )
        else:
            require(
                after["submitted"] - before["submitted"] == (5 if candidate else 10),
                "coalescing control not isolated",
            )
        require(
            peak["rss_bytes"] < 512 * 1024**2 and peak["fds"] < 64, "resource bound"
        )
        return {
            "status": "passed",
            "workload": workload,
            "candidate": candidate,
            "viewport_seconds": viewport,
            "foreground_seconds": [r[2] for r in results],
            "image_sha256": hashes[0],
            "peak": peak,
            "metrics": after,
            "settings": settings,
        }
    finally:
        gate.set()
        stop.set()
        sampler.join()
        app.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--without-coalescing", required=True, type=Path)
    parser.add_argument("--without-queued-expiry", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binaries = {
        "candidate": args.candidate.resolve(),
        "coalescing": args.without_coalescing.resolve(),
        "queued_expiry": args.without_queued_expiry.resolve(),
    }
    fixture = next(
        r for r in json.loads(MANIFEST.read_text())["fixtures"] if r["id"] == "red"
    )
    require(
        hashlib.sha256(RASTER.read_bytes()).hexdigest() == fixture["sha256"],
        "fixture checksum",
    )
    os.environ.pop("TC_PREFETCH_TILE_RING", None)
    template = out / "template.sqlite"
    operation(
        binaries["candidate"],
        out,
        {
            "operation": "create",
            "path": str(template),
            "keys": [{"key": "scene", "description": ""}],
        },
    )
    for scene in [
        "slow",
        *[f"obsolete-{i}" for i in range(4)],
        *[f"view-{i}" for i in range(4)],
    ]:
        operation(
            binaries["candidate"],
            out,
            {
                "operation": "insert",
                "path": str(template),
                "keys": [scene],
                "raster": str(RASTER),
            },
        )
    origin = Origin()
    origin.RequestHandlerClass = DelayedRangeHandler
    thread = threading.Thread(target=origin.serve_forever)
    thread.start()
    rows, summaries = [], {}
    try:
        for workload in ("queued_expiry", "coalescing"):
            for trial in range(3):
                for candidate in [True, False] if trial % 2 == 0 else [False, True]:
                    row = exercise(
                        binaries["candidate" if candidate else workload],
                        template,
                        origin,
                        out / f"{workload}-{trial}-{candidate}",
                        workload,
                        candidate,
                    )
                    row["trial"] = trial
                    rows.append(row)
            summaries[workload] = {}
            for candidate in (False, True):
                selected = [
                    r
                    for r in rows
                    if r["workload"] == workload and r["candidate"] == candidate
                ]
                require(
                    len({r["image_sha256"] for r in selected}) == 1,
                    "trial imagery differs",
                )
                durations = sorted(t for r in selected for t in r["foreground_seconds"])
                summaries[workload][str(candidate)] = {
                    "median_viewport_seconds": statistics.median(
                        r["viewport_seconds"] for r in selected
                    ),
                    "foreground_p95_seconds": durations[
                        math.ceil(0.95 * len(durations)) - 1
                    ],
                }
            require(
                len({r["image_sha256"] for r in rows if r["workload"] == workload})
                == 1,
                "control imagery differs",
            )
        report = {
            "binary_sha256": {
                name: hashlib.sha256(p.read_bytes()).hexdigest()
                for name, p in binaries.items()
            },
            "fixture_sha256": fixture["sha256"],
            "cases": rows,
            "summaries": summaries,
            "latency_improved": all(
                summary["True"]["median_viewport_seconds"]
                < summary["False"]["median_viewport_seconds"]
                and summary["True"]["foreground_p95_seconds"]
                < summary["False"]["foreground_p95_seconds"]
                for summary in summaries.values()
            ),
            "origin_requests": origin.requests,
            "origin_bytes": origin.bytes,
            "exclusions": [
                "Controlled loopback source delays are not WAN/S3 measurements",
                "Comparative pixel invariance is not an independent rendering oracle",
                "Blocked native signal draining and capsule joining are checked by request_cost.py",
            ],
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(summaries), flush=True)
        for summary in summaries.values():
            require(
                summary["True"]["median_viewport_seconds"]
                < summary["False"]["median_viewport_seconds"],
                "no viewport improvement",
            )
            require(
                summary["True"]["foreground_p95_seconds"]
                < summary["False"]["foreground_p95_seconds"],
                "no foreground tail improvement",
            )
    finally:
        for gate in origin.gates.values():
            gate.set()
        origin.shutdown()
        origin.server_close()
        thread.join()


if __name__ == "__main__":
    main()
