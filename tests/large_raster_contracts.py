"""Check full-source pixels and physical BigTIFF reads beyond 4 GiB."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import rasterio
import tifffile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/corpus/manifest.json"
LIMIT = 2**32


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def source_raster(out: Path, supplied: Path | None) -> tuple[Path, str]:
    fixture = next(
        row
        for row in json.loads(MANIFEST.read_text())["fixtures"]
        if row["id"] == "classes"
    )
    path = supplied.resolve() if supplied else out / "worldcover.tif"
    if not path.is_file():
        with (
            urllib.request.urlopen(fixture["source_url"], timeout=120) as response,
            path.open("wb") as stream,
        ):
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                stream.write(chunk)
    sha = digest(path)
    if sha != fixture["parent_sha256"]:
        raise AssertionError(f"source raster hash mismatch: {sha}")
    return path, sha


def build_and_verify(
    source: Path, derivative: Path
) -> tuple[int, int, int, float, float]:
    with rasterio.Env(GDAL_CACHEMAX=64), rasterio.open(source) as src:
        profile = src.profile.copy()
        profile.update(
            dtype="uint32",
            compress="none",
            BIGTIFF="YES",
            tiled=True,
            blockxsize=512,
            blockysize=512,
        )
        if not derivative.exists():
            with rasterio.open(derivative, "w", **profile) as dst:
                for _, window in dst.block_windows(1):
                    dst.write(
                        src.read(1, window=window).astype("uint32"), 1, window=window
                    )
        blocks = 0
        with rasterio.open(derivative) as dst:
            assert dst.width == src.width and dst.height == src.height
            assert dst.crs == src.crs and dst.transform == src.transform
            for _, window in dst.block_windows(1):
                expected = src.read(1, window=window)
                actual = dst.read(1, window=window)
                assert np.array_equal(expected, actual), f"pixel mismatch in {window}"
                assert np.array_equal(
                    src.read_masks(1, window=window),
                    dst.read_masks(1, window=window),
                ), f"mask mismatch in {window}"
                blocks += 1
        stat = derivative.stat()
        assert stat.st_size > LIMIT and stat.st_blocks * 512 > LIMIT
        with tifffile.TiffFile(derivative) as tif:
            assert tif.is_bigtiff
            offsets = tif.pages[0].dataoffsets
        columns = math.ceil(src.width / 512)
        for index, offset in enumerate(offsets):
            if offset <= LIMIT:
                continue
            row, col = divmod(index, columns)
            window = rasterio.windows.Window(col * 512, row * 512, 512, 512)
            values = src.read(1, window=window)
            if len(np.unique(values)) >= 3:
                lon, lat = src.xy(row * 512 + 256, col * 512 + 256)
                return blocks, stat.st_size, offset, lon, lat
    raise AssertionError("no varied WorldCover block starts above 4 GiB")


def operation(binary: Path, out: Path, request: dict) -> object:
    instruction = out / "operation.json"
    instruction.write_text(json.dumps(request))
    result = subprocess.run(
        [str(binary), "data-operation", str(instruction)],
        capture_output=True,
        text=True,
        check=True,
    )
    response = json.loads(result.stdout)
    if isinstance(response, dict) and "error" in response:
        raise AssertionError(response["error"])
    return response


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ready(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except (urllib.error.URLError, TimeoutError):
            if process.poll() is not None:
                raise RuntimeError("raster server exited") from None
            time.sleep(0.1)
    raise TimeoutError("raster server startup")


def rgba(url: str) -> np.ndarray:
    with urllib.request.urlopen(url, timeout=120) as response:
        assert response.status == 200
        with Image.open(io.BytesIO(response.read())) as image:
            return np.asarray(image.convert("RGBA")).copy()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "report.json").exists():
        raise FileExistsError(out / "report.json")
    started = time.monotonic()
    source, source_hash = source_raster(out, args.source)
    derivative = out / "worldcover-u32.tif"
    blocks, size, high_offset, lon, lat = build_and_verify(source, derivative)
    high_ranges: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_HEAD(self):
            self.respond(False)

        def do_GET(self):
            self.respond(True)

        def respond(self, body: bool):
            if self.path != "/worldcover-u32.tif":
                self.send_error(404)
                return
            header = self.headers.get("Range")
            if body and (not header or not header.startswith("bytes=")):
                self.send_error(416, "byte range required")
                return
            if header:
                beginning, ending = header[6:].split("-", 1)
                start = int(beginning)
                stop = min(int(ending) if ending else size - 1, size - 1)
                assert start <= stop < size
                high_ranges.append(start)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{stop}/{size}")
                length = stop - start + 1
            else:
                start, length = 0, size
                self.send_response(200)
            self.send_header("Content-Type", "image/tiff")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if body:
                with derivative.open("rb") as stream:
                    stream.seek(start)
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise AssertionError("raster ended inside byte range")
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

    range_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=range_server.serve_forever, daemon=True)
    thread.start()
    database = out / "catalog.sqlite"
    database.unlink(missing_ok=True)
    remote = f"http://127.0.0.1:{range_server.server_port}/worldcover-u32.tif"
    metadata = operation(
        binary,
        out,
        {
            "operation": "compute_metadata",
            "path": str(source),
            "options": {"max_shape": [512, 512]},
        },
    )
    operation(
        binary,
        out,
        {
            "operation": "create",
            "path": str(database),
            "keys": [{"key": "source", "description": ""}],
        },
    )
    for name, path in (
        ("parent", str(source)),
        ("local", str(derivative)),
        ("remote", remote),
    ):
        operation(
            binary,
            out,
            {
                "operation": "insert",
                "path": str(database),
                "keys": [name],
                "raster": path,
                "metadata": metadata,
            },
        )
    port = free_port()
    log = (out / "service.log").open("w")
    service = subprocess.Popen(
        [str(binary), "serve", "-d", str(database), "--bind", f"127.0.0.1:{port}"],
        stdout=log,
        stderr=log,
        env={**os.environ, "GDAL_CACHEMAX": "64", "TC_RASTER_QUEUE_CAPACITY": "16"},
    )
    try:
        ready(f"http://127.0.0.1:{port}/keys", service)
        zoom = 14
        scale = 2**zoom
        x = int((lon + 180) / 360 * scale)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)
        palette = {
            str(value): color
            for value, color in (
                (10, [0, 100, 0]),
                (20, [255, 187, 34]),
                (30, [255, 255, 76]),
                (40, [240, 150, 255]),
                (50, [250, 0, 0]),
                (60, [180, 180, 180]),
                (70, [240, 240, 240]),
                (80, [0, 100, 200]),
                (90, [0, 150, 160]),
                (95, [0, 207, 117]),
                (100, [250, 230, 160]),
            )
        }
        query = urllib.parse.urlencode(
            {
                "colormap": "explicit",
                "explicit_color_map": json.dumps(palette),
            }
        )
        images = {}
        for name in ("parent", "local", "remote"):
            path = f"/singleband/{name}/{zoom}/{x}/{y}.png?{query}"
            images[name] = rgba(f"http://127.0.0.1:{port}{path}")
        assert np.array_equal(images["parent"], images["local"])
        assert np.array_equal(images["local"], images["remote"])
        assert max(high_ranges, default=0) > LIMIT, high_ranges
        report = {
            "passed": True,
            "source_sha256": source_hash,
            "derivative_sha256": digest(derivative),
            "bytes": size,
            "blocks_verified": blocks,
            "selected_offset": high_offset,
            "max_http_range_start": max(high_ranges),
            "ranged_requests": len(high_ranges),
            "image_sha256": hashlib.sha256(images["remote"].tobytes()).hexdigest(),
            "seconds": round(time.monotonic() - started, 2),
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        service.terminate()
        service.wait(timeout=10)
        log.close()
        range_server.shutdown()
        range_server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
