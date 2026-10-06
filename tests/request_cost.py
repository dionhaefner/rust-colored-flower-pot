"""Stage metrics, queued expiry, coalescing, disconnects and owned shutdown.

Uses checksum-pinned real Sentinel pixels. A controlled range server delays HEAD
responses to make contention reproducible; it never fabricates raster samples.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.server
import json
import math
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path

from resource_contracts import (
    MANIFEST,
    RASTER,
    fetch,
    free_port,
    operation,
    ready,
    require,
)

STAGES = {
    "selection",
    "queue_wait",
    "source_access",
    "raster_read",
    "coloring",
    "encoding",
    "render_wait",
    "request",
    "delivery",
    "prefetch",
    "http_queue_wait",
    "remote_source_access",
    "remote_raster_read",
}


def until(predicate, message, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


class Origin(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), RangeHandler)
        self.lock = threading.Lock()
        self.gates = {}
        self.seen = {}
        self.requests = 0
        self.bytes = 0
        self.raster = RASTER.read_bytes()

    def gate(self, name):
        with self.lock:
            self.gates[name] = threading.Event()
            self.seen[name] = threading.Event()
        return self.gates[name], self.seen[name]


class RangeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.reply(False)

    def do_GET(self):
        self.reply(True)

    def reply(self, body):
        origin = self.server
        name = self.path.split("?")[0].strip("/")
        with origin.lock:
            origin.requests += 1
            gate = origin.gates.get(name)
            seen = origin.seen.get(name)
        if seen:
            seen.set()
        if gate and not gate.wait(10):
            self.send_error(504)
            return
        if name.startswith("error"):
            self.send_error(500)
            return
        if not name.endswith(".tif"):
            self.send_error(404)
            return
        data = origin.raster
        start, end = 0, len(data) - 1
        range_header = self.headers.get("Range")
        if range_header:
            first, last = range_header.removeprefix("bytes=").split("-")
            start, end = int(first), min(int(last) if last else end, end)
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.end_headers()
        if body:
            try:
                self.wfile.write(data[start : end + 1])
                with origin.lock:
                    origin.bytes += end - start + 1
            except (BrokenPipeError, ConnectionResetError):
                pass


class Native:
    def __init__(self, binary, database, settings, out, bind_host="127.0.0.1"):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = (out / "service.log").open("w")
        self.process = subprocess.Popen(
            [
                str(binary),
                "serve",
                "-d",
                str(database),
                "--bind",
                f"{bind_host}:{self.port}",
            ],
            stdout=self.log,
            stderr=self.log,
            env={
                **os.environ,
                **{
                    f"TC_{k}": str(v).lower() if isinstance(v, bool) else str(v)
                    for k, v in settings.items()
                },
            },
        )
        ready(self.base + "/keys", self.process)

    def request(self, path):
        return fetch(self.base + path)

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=10)
        self.log.close()


class PythonApplication:
    def __init__(self, database, settings):
        import rcfp

        self.app = rcfp.NativeApplication(database, settings)

    def request(self, path):
        status, _, data, _, _ = self.app.request("GET", path)
        return status, data

    def close(self):
        # Releasing the actual capsule exercises native pool joining.
        del self.app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument(
        "--python-package", type=Path, default=Path(sysconfig.get_path("purelib"))
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.python_package.resolve()))
    fixture = next(
        row
        for row in json.loads(MANIFEST.read_text())["fixtures"]
        if row["id"] == "red"
    )
    require(
        hashlib.sha256(RASTER.read_bytes()).hexdigest() == fixture["sha256"],
        "pinned raster checksum",
    )
    template = out / "template.sqlite"
    operation(
        binary,
        out,
        {
            "operation": "create",
            "path": str(template),
            "keys": [{"key": "scene", "description": ""}],
        },
    )
    for name in ("local", "slow", "obsolete", "error", "duplicate", "shutdown"):
        operation(
            binary,
            out,
            {
                "operation": "insert",
                "path": str(template),
                "keys": [name],
                "raster": str(RASTER),
            },
        )
    origin = Origin()
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    rows = []
    settings = {
        "RASTER_WORKERS": 1,
        "RASTER_QUEUE_CAPACITY": 16,
        "RASTER_TIMEOUT_MS": 500,
        "RASTER_CACHE_BYTES": 0,
        "RENDER_CACHE_BYTES": 0,
        "EXPOSE_METRICS": True,
        "DEBUG": False,
        "REMOTE_SOURCE_CACHE_TTL_SECONDS": 0,
    }
    tile = lambda name, size=256: (
        f"/singleband/{name}/preview.png?stretch_range=%5B0%2C3000%5D&tile_size=%5B{size}%2C{size}%5D"
    )

    def metrics(app):
        status, body = app.request("/__terracotta/metrics")
        require(status == 200, "metrics status")
        return json.loads(body)

    def drained(app):
        m = metrics(app)
        return (
            m["queued"] == m["in_flight"] == 0
            and m["submitted"] == m["completed"] + m["failed"] + m["cancelled_queued"]
        )

    try:
        for mode in ("native", "python"):
            mode_out = out / mode
            mode_out.mkdir()
            db = mode_out / "catalog.sqlite"
            shutil.copyfile(template, db)
            names = {
                name: f"{mode}-{name}.tif"
                for name in ("slow", "obsolete", "duplicate", "shutdown")
            }
            names["error"] = f"error-{mode}.tif"
            with sqlite3.connect(db) as conn:
                for name, path in names.items():
                    conn.execute(
                        "UPDATE datasets SET path=? WHERE scene=?",
                        (
                            f"http://127.0.0.1:{origin.server_port}/{path}?token=private-signed-value",
                            name,
                        ),
                    )
            app = (
                Native(binary, db, {**settings, "DEBUG": True}, mode_out)
                if mode == "native"
                else PythonApplication(db, settings)
            )
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                    gate, seen = origin.gate(names["slow"])
                    slow = pool.submit(app.request, tile("slow"))
                    require(seen.wait(5), "slow source never began")
                    obsolete_gate, obsolete_seen = origin.gate(names["obsolete"])
                    obsolete = pool.submit(app.request, tile("obsolete"))
                    until(
                        lambda app=app: metrics(app)["queued"] == 1,
                        "obsolete request not queued",
                    )
                    require(
                        slow.result()[0] == obsolete.result()[0] == 504,
                        "wait deadline status",
                    )
                    m = metrics(app)
                    require(
                        m["in_flight"] == 1 and m["queued"] == 1,
                        "deadline incorrectly cancelled executing work",
                    )
                    local = pool.submit(app.request, tile("local"))
                    started = time.monotonic()
                    gate.set()
                    require(
                        local.result()[0] == 200,
                        "foreground request after expiry failed",
                    )
                    elapsed = time.monotonic() - started
                    until(lambda app=app: drained(app), "admitted work did not drain")
                    m = metrics(app)
                    require(m["cancelled_queued"] == 1, "queued cancellation count")
                    require(not obsolete_seen.is_set(), "obsolete read reached origin")
                    obsolete_gate.set()
                    require(
                        m["cost"]["stages"]["source_access"]["sum_seconds"]
                        >= settings["RASTER_TIMEOUT_MS"] / 1000,
                        "slow source access not measured",
                    )
                    require(
                        m["cost"]["stages"]["queue_wait"]["sum_seconds"]
                        >= settings["RASTER_TIMEOUT_MS"] / 1000,
                        "queue wait not measured",
                    )
                    rows.append(
                        {
                            "mode": mode,
                            "case": "expired_wait_and_foreground",
                            "status": "passed",
                            "foreground_seconds": elapsed,
                            "metrics": m,
                        }
                    )
                    require(app.request(tile("error"))[0] == 400, "error source status")
                    until(lambda app=app: drained(app), "error source accounting")
                    # Duplicate URLs share active work even with both retention budgets zero.
                    gate, seen = origin.gate(names["duplicate"])
                    baseline = metrics(app)
                    leader = pool.submit(app.request, tile("duplicate"))
                    require(seen.wait(5), "duplicate leader did not begin")
                    followers = [
                        pool.submit(app.request, tile("duplicate")) for _ in range(5)
                    ]
                    until(
                        lambda app=app, baseline=baseline: (
                            metrics(app)["render_cache"]["coalesced"]
                            >= baseline["render_cache"]["coalesced"] + 5
                        ),
                        "duplicates did not coalesce",
                    )
                    gate.set()
                    results = [f.result() for f in [leader, *followers]]
                    require(
                        all(
                            status == 200 and data == results[0][1]
                            for status, data in results
                        ),
                        "coalesced result mismatch",
                    )
                    m = metrics(app)
                    require(
                        m["submitted"] == baseline["submitted"] + 1,
                        "duplicate native reads",
                    )
                    require(
                        m["render_cache"]["entries"] == 0,
                        "zero budget retained finished images",
                    )
                    require(
                        m["cost"]["stages"]["render_wait"]["count"] >= 5,
                        "coalesced waits not measured",
                    )
                    stages = m["cost"]["stages"]
                    require(set(stages) == STAGES, "unbounded stage labels")
                    require(
                        all(
                            stages[name]["count"] > 0
                            for name in STAGES
                            - {"delivery", "prefetch", "http_queue_wait"}
                        ),
                        "missing stage observations",
                    )
                    status, prom = app.request("/__terracotta/metrics/prometheus")
                    require(
                        status == 200
                        and b'rcfp_stage_duration_seconds_bucket{stage="queue_wait",le="+Inf"}'
                        in prom,
                        "Prometheus histogram missing",
                    )
                    require(
                        b"private-signed-value" not in prom
                        and "private-signed-value" not in json.dumps(m),
                        "secret in metrics",
                    )
                    rows.append(
                        {
                            "mode": mode,
                            "case": "errors_coalescing_and_stages",
                            "status": "passed",
                            "metrics": m,
                        }
                    )
                    if mode == "native":
                        # A socket disconnect does not prove that GDAL has stopped.
                        gate, seen = origin.gate(names["shutdown"])
                        with socket.create_connection(("127.0.0.1", app.port)) as sock:
                            sock.sendall(
                                f"GET {tile('shutdown')} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
                            )
                            require(seen.wait(5), "disconnect read did not begin")
                        until(
                            lambda app=app: metrics(app)["timed_out"] >= 3,
                            "disconnect waiter did not expire",
                        )
                        require(
                            metrics(app)["in_flight"] == 1,
                            "disconnect lost active accounting",
                        )
                        gate.set()
                        until(
                            lambda app=app: drained(app),
                            "disconnect work did not drain",
                        )
                        app.request(
                            "/missing/private-signed-value?token=private-signed-value"
                        )
                        app.log.flush()
                        logs = (mode_out / "service.log").read_text()
                        require(
                            "private-signed-value" not in logs
                            and "stages_seconds" in logs,
                            "request timing log redaction",
                        )
                        rows.append(
                            {
                                "mode": mode,
                                "case": "disconnect_drain",
                                "status": "passed",
                            }
                        )
                    else:
                        gate, seen = origin.gate(names["shutdown"])
                        active = pool.submit(app.request, tile("shutdown"))
                        require(seen.wait(5), "shutdown read did not begin")
                        require(active.result()[0] == 504, "shutdown deadline")
                        closed = pool.submit(app.close)
                        time.sleep(0.05)
                        require(
                            not closed.done(),
                            "application destruction detached active native work",
                        )
                        gate.set()
                        closed.result(timeout=5)
                        app = None
                        rows.append(
                            {
                                "mode": mode,
                                "case": "owned_shutdown_join",
                                "status": "passed",
                            }
                        )
            finally:
                if app is not None:
                    for event in origin.gates.values():
                        event.set()
                    app.close()
        # Distinguish shared render admission saturation from an expired native wait.
        for mode in ("native", "python"):
            saturated_out = out / f"{mode}-saturation"
            saturated_out.mkdir()
            db = saturated_out / "catalog.sqlite"
            shutil.copyfile(template, db)
            source = f"{mode}-saturation.tif"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE datasets SET path=? WHERE scene='slow'",
                    (f"http://127.0.0.1:{origin.server_port}/{source}",),
                )
            limits = {**settings, "RASTER_QUEUE_CAPACITY": 1, "RASTER_TIMEOUT_MS": 500}
            saturated = (
                Native(binary, db, limits, saturated_out)
                if mode == "native"
                else PythonApplication(db, limits)
            )
            gate, seen = origin.gate(source)
            try:
                require(
                    metrics(saturated)["submitted"] == 0,
                    "application counters leaked across owners",
                )
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    running = pool.submit(saturated.request, tile("slow"))
                    require(seen.wait(5), "saturation source did not begin")
                    queued = pool.submit(saturated.request, tile("local"))
                    # Let the render reach the one-slot HTTP queue before its observer.
                    time.sleep(0.05)
                    until(
                        lambda saturated=saturated: metrics(saturated)["queued"] == 1,
                        "bounded queue did not fill",
                    )
                    require(
                        saturated.request(tile("local", 192))[0] == 503,
                        "render admission did not reject excess work",
                    )
                    require(
                        metrics(saturated)["cost"]["render_overloaded"] == 1,
                        "render rejection counter missing",
                    )
                    require(
                        running.result()[0] == queued.result()[0] == 504,
                        "saturated wait did not expire",
                    )
                    gate.set()
                    until(
                        lambda saturated=saturated: drained(saturated),
                        "saturated work did not drain",
                    )
                rows.append(
                    {
                        "case": "admission_saturation",
                        "mode": mode,
                        "status": "passed",
                        "metrics": metrics(saturated),
                    }
                )
            finally:
                gate.set()
                saturated.close()
        if os.name == "posix":
            for shutdown_signal in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal_out = out / shutdown_signal.name.lower()
                signal_out.mkdir()
                db = signal_out / "catalog.sqlite"
                shutil.copyfile(template, db)
                active_name = f"shutdown-{shutdown_signal.name}.tif"
                queued_name = f"queued-{shutdown_signal.name}.tif"
                with sqlite3.connect(db) as conn:
                    for scene, name in (
                        ("slow", active_name),
                        ("obsolete", queued_name),
                    ):
                        conn.execute(
                            "UPDATE datasets SET path=? WHERE scene=?",
                            (f"http://127.0.0.1:{origin.server_port}/{name}", scene),
                        )
                stopping = Native(
                    binary,
                    db,
                    settings,
                    signal_out,
                    bind_host="0.0.0.0"
                    if shutdown_signal == signal.SIGHUP
                    else "127.0.0.1",
                )
                gate, seen = origin.gate(active_name)
                queued_gate, queued_seen = origin.gate(queued_name)
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        try:
                            active = pool.submit(stopping.request, tile("slow"))
                            require(seen.wait(5), "shutdown read never began")
                            queued = pool.submit(stopping.request, tile("obsolete"))
                            until(
                                lambda stopping=stopping: (
                                    metrics(stopping)["queued"] == 1
                                ),
                                "shutdown work not queued",
                            )
                            require(
                                active.result()[0] == queued.result()[0] == 504,
                                "shutdown deadlines",
                            )
                            pending = metrics(stopping)
                            require(
                                pending["in_flight"] == pending["queued"] == 1,
                                "shutdown lost native work",
                            )
                            stopping.process.send_signal(shutdown_signal)

                            def listener_closed(stopping=stopping):
                                try:
                                    with socket.create_connection(
                                        ("127.0.0.1", stopping.port), timeout=0.1
                                    ):
                                        return False
                                except ConnectionRefusedError:
                                    return True

                            until(
                                listener_closed,
                                "shutdown did not close HTTP admission",
                                seconds=5,
                            )
                            time.sleep(0.05)
                            require(
                                stopping.process.poll() is None,
                                "shutdown detached active native work",
                            )
                            gate.set()
                            require(
                                stopping.process.wait(timeout=5) == 0,
                                "shutdown exit status",
                            )
                            require(
                                not queued_seen.is_set(),
                                "shutdown executed obsolete queued work",
                            )
                        finally:
                            gate.set()
                            queued_gate.set()
                    rows.append(
                        {
                            "case": "native_signal_join",
                            "signal": shutdown_signal.name,
                            "status": "passed",
                            "pending": pending,
                        }
                    )
                finally:
                    gate.set()
                    queued_gate.set()
                    stopping.close()
        # Foreground tile load with actual idle neighboring-tile reads, under equal budgets.
        lon = sum(fixture["geographic_bounds"][::2]) / 2
        lat = sum(fixture["geographic_bounds"][1::2]) / 2
        zoom, scale = 16, 2**16
        x = int((lon + 180) / 360 * scale)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)
        paths = [
            f"/singleband/local/{zoom}/{x + dx}/{y + dy}.png?stretch_range=%5B0%2C3000%5D"
            for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]
        ]
        prefetch_trials = []
        expected = None
        for trial in range(3):
            for enabled in [False, True] if trial % 2 == 0 else [True, False]:
                if enabled:
                    os.environ["TC_PREFETCH_TILE_RING"] = "1"
                else:
                    os.environ.pop("TC_PREFETCH_TILE_RING", None)
                foreground = PythonApplication(
                    template,
                    {**settings, "RASTER_WORKERS": 2, "RASTER_TIMEOUT_MS": 5000},
                )
                try:
                    initial = [foreground.request(path) for path in paths]
                    require(
                        all(status == 200 for status, _ in initial),
                        "prefetch setup tiles",
                    )
                    hashes = [hashlib.sha256(body).hexdigest() for _, body in initial]
                    if expected is None:
                        expected = hashes
                    require(hashes == expected, "prefetch changed foreground pixels")
                    if enabled:
                        until(
                            lambda foreground=foreground: (
                                metrics(foreground)["prefetch_completed"] > 0
                            ),
                            "idle prefetch did not execute",
                        )

                    def foreground_read(
                        index, foreground=foreground, expected=expected
                    ):
                        start = time.monotonic()
                        status, body = foreground.request(paths[index % len(paths)])
                        require(
                            status == 200
                            and hashlib.sha256(body).hexdigest()
                            == expected[index % len(paths)],
                            "foreground under prefetch contention",
                        )
                        return time.monotonic() - start

                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                        latencies = list(pool.map(foreground_read, range(160)))
                    until(
                        lambda foreground=foreground: drained(foreground),
                        "prefetch foreground accounting",
                    )
                    m = metrics(foreground)
                    require(
                        m["prefetch_in_flight"] <= m["workers"]
                        and m["cache_bytes"] <= m["cache_capacity_bytes"],
                        "prefetch exceeded budgets",
                    )
                    prefetch_trials.append(
                        {
                            "trial": trial,
                            "enabled": enabled,
                            "p95_seconds": sorted(latencies)[
                                int(0.95 * len(latencies))
                            ],
                            "viewport_seconds": sum(latencies),
                            "metrics": m,
                        }
                    )
                finally:
                    foreground.close()
        os.environ.pop("TC_PREFETCH_TILE_RING", None)
        rows.append(
            {
                "case": "foreground_and_prefetch",
                "status": "passed",
                "trials": prefetch_trials,
            }
        )
        # Exercise the actual X-Ray datagram serializer, including worker stage propagation.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as daemon:
            daemon.bind(("127.0.0.1", 0))
            daemon.settimeout(2)
            os.environ["AWS_XRAY_DAEMON_ADDRESS"] = (
                f"127.0.0.1:{daemon.getsockname()[1]}"
            )
            traced = PythonApplication(template, {**settings, "XRAY_PROFILE": True})
            try:
                header = {
                    "x-amzn-trace-id": "Root=1-12345678-123456789012345678901234;Parent=1234567890123456;Sampled=1"
                }
                require(
                    traced.app.request("GET", tile("local"), header)[0] == 200,
                    "traced render status",
                )
                datagram = daemon.recv(65536).split(b"\n", 1)[1]
                segment = json.loads(datagram)
                require(
                    segment["http"]["request"]["url"] == "/singleband",
                    "trace route contains dataset identity",
                )
                require(
                    set(segment["metadata"]["rcfp"]["stages_seconds"]) == STAGES,
                    "trace stages",
                )
                require(
                    segment["metadata"]["rcfp"]["stages_seconds"]["raster_read"] > 0,
                    "worker stages missing in trace",
                )
                traced.app.request(
                    "GET",
                    "/missing/private-signed-value?token=private-signed-value",
                    header,
                )
                datagram = daemon.recv(65536)
                require(b"private-signed-value" not in datagram, "secret in trace")
            finally:
                traced.close()
                os.environ.pop("AWS_XRAY_DAEMON_ADDRESS", None)
        rows.append(
            {"case": "xray_stage_propagation_and_redaction", "status": "passed"}
        )
        # Monitoring protection is shared by native/Python applications.
        hidden = PythonApplication(template, {**settings, "EXPOSE_METRICS": False})
        try:
            require(
                all(
                    hidden.request(path)[0] == 404
                    for path in (
                        "/__terracotta/metrics",
                        "/__terracotta/metrics/prometheus",
                    )
                ),
                "metrics exposed by default",
            )
        finally:
            hidden.close()
        rows.append({"case": "monitoring_protection", "status": "passed"})
        report = {
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "raster_sha256": fixture["sha256"],
            "cases": rows,
            "origin_requests": origin.requests,
            "origin_bytes": origin.bytes,
            "skips": []
            if os.name == "posix"
            else ["Unix SIGTERM/SIGINT/SIGHUP shutdown gate"],
            "exclusions": [
                "Windows console shutdown and forceful process termination are not exercised by the Unix signal gate",
                "Delivery measures native socket writes only; Python/WSGI delivery is host-owned",
                "Controlled origin traffic is not actual S3 or WAN traffic",
            ],
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps({"passed": len(rows), "origin_requests": origin.requests}),
            flush=True,
        )
    finally:
        for event in origin.gates.values():
            event.set()
        origin.shutdown()
        origin.server_close()
        origin_thread.join(timeout=5)


if __name__ == "__main__":
    main()
