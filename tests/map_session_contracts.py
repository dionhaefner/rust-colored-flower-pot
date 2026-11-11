"""Qualify browser timing against synthetic tiles with controlled response delays."""

import argparse
import asyncio
import functools
import io
import json
import shutil
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from map_sessions import ROOT, run_case
from PIL import Image


class TileServer(ThreadingHTTPServer):
    request_queue_size = 256

    def handle_error(self, request, client_address):
        # Interrupted map requests can close a persistent socket between reads.
        import sys

        if not isinstance(sys.exception(), (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


class Tiles(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    delay = 0
    png = b""

    def log_message(self, *_):
        pass

    def do_GET(self):
        if urlsplit(self.path).path.endswith(".png"):
            time.sleep(self.delay)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(self.png)))
            self.end_headers()
            try:
                self.wfile.write(self.png)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    buffer = io.BytesIO()
    Image.new("RGBA", (256, 256), (30, 150, 60, 255)).save(buffer, format="PNG")
    Tiles.png = buffer.getvalue()
    web = args.out / "web"
    web.mkdir()
    shutil.copy2(ROOT / "tests/benchmarks/map-session.html", web / "index.html")
    for name in ("leaflet.css", "leaflet.js"):
        shutil.copy2(ROOT / "src/assets/client/vendor" / name, web / name)
    server = TileServer(
        ("127.0.0.1", 0), functools.partial(Tiles, directory=str(web.resolve()))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    checks = []
    try:
        for name, delay, users in (
            ("fast", 0, 2),
            ("slow", 0.45, 2),
            ("eight-users", 0, 8),
            ("disjoint-users", 0, 8),
        ):
            Tiles.delay = delay
            folder = args.out / name
            folder.mkdir()
            result = asyncio.run(
                run_case(
                    origin,
                    origin,
                    users,
                    "singleband",
                    folder,
                    spec={
                        "actions": 3,
                        "interval_ms": 1000,
                        "episodes": 1,
                        "gestures": 4,
                        "gesture_ms": 150,
                        "episode_ms": 4000,
                        "styles": 2,
                        "style_interval_ms": 1000,
                    },
                    arrangement="disjoint"
                    if name == "disjoint-users"
                    else "overlapping",
                )
            )
            (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            assert not result["javascript_errors"]
            assert result["summary"]["paced"]["targets"] == users * 3
            assert result["summary"]["rapid"]["targets"] == users
            assert result["summary"]["style"]["targets"] == users * 2
            if not delay:
                assert result["summary"]["paced"]["completion_fraction"] == 1
                assert result["summary"]["rapid"]["completion_fraction"] == 1
            else:
                assert result["summary"]["paced"]["completion_fraction"] < 1
            checks.append({"name": name, "passed": True, "summary": result["summary"]})
    finally:
        server.shutdown()
        server.server_close()
        (args.out / "report.json").write_text(
            json.dumps(
                {
                    "passed": len(checks) == 4,
                    "checks": checks,
                    "fixture": "Synthetic solid-color 256-pixel tiles isolate measurement and browser behavior; these are not raster correctness references.",
                },
                indent=2,
            )
            + "\n"
        )
    print(f"Passed {len(checks)} browser measurement contracts.")


if __name__ == "__main__":
    main()
