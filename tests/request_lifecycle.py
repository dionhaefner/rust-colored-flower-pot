"""Correctness of HTTP deadlines, disconnects, bounded admission and recovery.

The shared controlled origin serves checksum-pinned real raster bytes. Gates
hold source reads until accounting assertions finish; elapsed times are not
performance results.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import socket
import sqlite3
import threading
from pathlib import Path

from request_cost import Native, Origin, until
from resource_contracts import MANIFEST, RASTER, operation, require


def metrics(app):
    status, body = app.request("/__terracotta/metrics")
    require(status == 200, f"metrics unavailable: HTTP {status}, {body!r}")
    return json.loads(body)


def drained(app):
    m = metrics(app)
    return (
        m["in_flight"] == m["queued"] == m["render_cache"]["in_flight"] == 0
        and m["render_admitted"] == 0
        and m["submitted"] == m["completed"] + m["failed"] + m["cancelled_queued"]
    )


def tile(name):
    return f"/singleband/{name}/preview.png?stretch_range=%5B0%2C3000%5D"


def recovered(app):
    status, body = app.request(tile("local"))
    require(
        status == 200 and body.startswith(b"\x89PNG\r\n\x1a\n"),
        "recovery render failed",
    )
    until(lambda: drained(app), "recovery accounting did not drain")


def exercise(binary, out, origin, profile):
    database = out / "catalog.sqlite"
    operation(
        binary,
        out,
        {
            "operation": "create",
            "path": str(database),
            "keys": [{"key": "scene", "description": ""}],
        },
    )
    names = ("local", "slow", "obsolete", "disconnect")
    for name in names:
        operation(
            binary,
            out,
            {
                "operation": "insert",
                "path": str(database),
                "keys": [name],
                "raster": str(RASTER),
            },
        )
    sources = {name: f"{profile}-{name}.tif" for name in names[1:]}
    with sqlite3.connect(database) as connection:
        for name, source in sources.items():
            connection.execute(
                "UPDATE datasets SET path=? WHERE scene=?",
                (f"http://127.0.0.1:{origin.server_port}/{source}", name),
            )
    app = Native(
        binary,
        database,
        {
            "RASTER_WORKERS": 1,
            "RASTER_QUEUE_CAPACITY": 4,
            "RASTER_TIMEOUT_MS": 1000,
            "RASTER_CACHE_BYTES": 0,
            "RENDER_CACHE_BYTES": 0,
            "SOURCE_BLOCK_CACHE_BYTES": 0,
            "REMOTE_SOURCE_CACHE_TTL_SECONDS": 0,
            "EXPOSE_METRICS": True,
            "DEBUG": True,
        },
        out,
    )
    rows = []
    try:
        slow_gate, slow_seen = origin.gate(sources["slow"])
        obsolete_gate, obsolete_seen = origin.gate(sources["obsolete"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            try:
                slow = pool.submit(app.request, tile("slow"))
                require(slow_seen.wait(5), "active source read never began")
                obsolete = [
                    pool.submit(
                        app.request,
                        tile("obsolete") + f"&tile_size=%5B{size}%2C{size}%5D",
                    )
                    for size in (128, 192, 256, 320)
                ]
                until(
                    lambda: metrics(app)["queued"] == 4,
                    "renders did not fill the queue",
                )
                # All render permits are held. This request must be rejected,
                # without a retry or an additional native job.
                require(
                    app.request(tile("local"))[0] == 503,
                    "full admission did not reject",
                )
                full = metrics(app)
                require(
                    full["submitted"] == 5 and full["in_flight"] == 1,
                    "rejection admitted work",
                )
                require(
                    full["cost"]["render_overloaded"] == 1,
                    "render rejection not counted",
                )
                statuses = [future.result(timeout=5)[0] for future in [slow, *obsolete]]
                require(statuses == [504] * 5, "expired requests did not return 504")
                expired = metrics(app)
                require(
                    expired["in_flight"] == 1
                    and expired["queued"] == 4
                    and expired["completed"] == expired["failed"] == 0
                    and expired["timed_out"] == 5,
                    "response deadlines lost admitted native work",
                )
                # HTTP permits have been released, but the native queue is still
                # full. A new request cannot bypass the outstanding work.
                require(
                    app.request(tile("local"))[0] == 503,
                    "expired work freed native capacity",
                )
                rejected = metrics(app)
                require(
                    rejected["submitted"] == 5
                    and rejected["queued"] == 4
                    and rejected["in_flight"] == 1
                    and rejected["overloaded"] == 1,
                    "native rejection was unbounded or unaccounted",
                )
                slow_gate.set()
                until(lambda: drained(app), "expired work did not drain")
                final = metrics(app)
                require(
                    final["completed"] == 1
                    and final["cancelled_queued"] == 4
                    and final["failed"] == 0
                    and not obsolete_seen.is_set(),
                    "obsolete queued work reached the source or escaped accounting",
                )
                recovered(app)
                rows.append(
                    {
                        "case": "deadline_queued_expiry_and_recovery",
                        "status": "passed",
                        "http_statuses": statuses,
                        "expired_metrics": expired,
                        "drained_metrics": final,
                        "recovered_metrics": metrics(app),
                    }
                )
                rows.append(
                    {
                        "case": "bounded_rejections",
                        "status": "passed",
                        "http_statuses": [503, 503],
                        "full_metrics": full,
                        "rejected_metrics": rejected,
                    }
                )
            finally:
                slow_gate.set()
                obsolete_gate.set()

        gate, seen = origin.gate(sources["disconnect"])
        before = metrics(app)
        try:
            with socket.create_connection(("127.0.0.1", app.port), timeout=5) as sock:
                sock.sendall(
                    f"GET {tile('disconnect')} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
                )
                require(seen.wait(5), "disconnect source read never began")
                sock.shutdown(socket.SHUT_RDWR)
            until(
                lambda: metrics(app)["timed_out"] == before["timed_out"] + 1,
                "disconnected request waiter never expired",
            )
            abandoned = metrics(app)
            require(
                abandoned["in_flight"] == 1
                and abandoned["queued"] == 0
                and abandoned["submitted"] == before["submitted"] + 1
                and abandoned["completed"] == before["completed"],
                "disconnect lost executing native work",
            )
            gate.set()
            until(lambda: drained(app), "disconnected work did not drain")
            final = metrics(app)
            require(
                final["completed"] == before["completed"] + 1
                and final["failed"] == before["failed"],
                "disconnected job did not finish exactly once",
            )
            recovered(app)
            rows.append(
                {
                    "case": "disconnect_accounting_and_recovery",
                    "status": "passed",
                    "abandoned_metrics": abandoned,
                    "drained_metrics": final,
                    "recovered_metrics": metrics(app),
                }
            )
        finally:
            gate.set()
        return rows
    finally:
        for event in origin.gates.values():
            event.set()
        app.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = next(
        row
        for row in json.loads(MANIFEST.read_text())["fixtures"]
        if row["id"] == "red"
    )
    require(
        hashlib.sha256(RASTER.read_bytes()).hexdigest() == fixture["sha256"],
        "raster checksum changed",
    )
    original_environment = dict(os.environ)
    os.environ.pop("TC_PREFETCH_TILE_RING", None)
    report = {
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "origin_helper_sha256": hashlib.sha256(
            Path(__file__).with_name("request_cost.py").read_bytes()
        ).hexdigest(),
        "raster_sha256": fixture["sha256"],
        "platform": platform.platform(),
        "cases": [],
        "exclusions": [
            "Controlled source delays are correctness inputs, not S3/WAN performance evidence",
            "Socket disconnect coverage uses native HTTP; host-owned WSGI delivery is separate",
            "Blocked native signal draining and capsule joining are covered by request_cost.py",
        ],
    }
    origin = Origin()
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    try:
        for profile in ("native", "terracotta-0.10.1"):
            os.environ["RCFP_COMPAT_PROFILE"] = profile
            folder = out / profile
            folder.mkdir()
            try:
                rows = exercise(binary, folder, origin, profile)
                report["cases"].extend({"profile": profile, **row} for row in rows)
            except Exception as error:  # noqa: BLE001 - retain each profile's failure
                report["cases"].append(
                    {"profile": profile, "status": "failed", "error": str(error)}
                )
            (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        failures = [row for row in report["cases"] if row["status"] != "passed"]
        print(
            json.dumps(
                {"passed": len(report["cases"]) - len(failures), "failures": failures}
            ),
            flush=True,
        )
        require(not failures, "request lifecycle correctness failed; see report.json")
    finally:
        for event in origin.gates.values():
            event.set()
        origin.shutdown()
        origin.server_close()
        thread.join(timeout=5)
        os.environ.clear()
        os.environ.update(original_environment)


if __name__ == "__main__":
    main()
