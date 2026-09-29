"""Compare local and byte-range raster reads through the installed package."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RASTER = ROOT / "tests/fixtures/corpus/red.tif"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    import rcfp

    fixture = next(
        row
        for row in json.loads(
            (ROOT / "tests/fixtures/corpus/manifest.json").read_text()
        )["fixtures"]
        if row["id"] == "red"
    )
    require(
        hashlib.sha256(RASTER.read_bytes()).hexdigest() == fixture["sha256"],
        "raster fixture changed",
    )
    requests = []
    payload = RASTER.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def respond(self, send_body):
            if self.path != "/red.tif":
                self.send_error(404)
                return
            header = self.headers.get("Range")
            if header and header.startswith("bytes="):
                bounds = header[6:].split("-", 1)
                start = int(bounds[0])
                stop = int(bounds[1]) if bounds[1] else len(payload) - 1
                stop = min(stop, len(payload) - 1)
                data = payload[start : stop + 1]
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{stop}/{len(payload)}"
                )
            else:
                data = payload
                self.send_response(200)
            self.send_header("Content-Type", "image/tiff")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            requests.append(
                {"method": self.command, "range": header, "bytes": len(data)}
            )
            if send_body:
                self.wfile.write(data)

        def do_HEAD(self):
            self.respond(False)

        def do_GET(self):
            self.respond(True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        database = out / "catalog.sqlite"
        store = rcfp.DataStore(database)
        store.create(["location", "source"])
        store.insert(["lagoon", "local"], RASTER)
        url = f"http://127.0.0.1:{server.server_port}/red.tif"
        store.insert(["lagoon", "remote"], url)
        images = {}
        for name in ("local", "remote"):
            status, kind, body, _ = rcfp.request(
                database,
                "GET",
                f"/singleband/lagoon/{name}/preview.png?stretch_range=%5B0%2C3000%5D",
            )
            require(status == 200 and kind == "image/png", f"{name} request failed")
            with Image.open(io.BytesIO(body)) as image:
                images[name] = np.asarray(image.convert("RGBA")).copy()
        require(
            np.array_equal(images["local"], images["remote"]),
            "local and remote decoded images differ",
        )
        require(
            any(request["range"] is not None for request in requests),
            "remote reader sent no byte ranges",
        )
        report = {
            "package_version": rcfp.__version__,
            "requests": requests,
            "ranged_requests": sum(
                request["range"] is not None for request in requests
            ),
            "decoded_image_sha256": hashlib.sha256(
                images["remote"].tobytes()
            ).hexdigest(),
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "requests": len(requests),
                    "ranged_requests": report["ranged_requests"],
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
