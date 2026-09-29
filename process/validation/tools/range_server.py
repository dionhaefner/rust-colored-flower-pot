"""Instrumented local HTTP byte-range server used by P02 validation."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlsplit


class RangeServer:
    def __init__(self, root: Path, port: int, delay_seconds: float = 0.0):
        self.root = root.resolve()
        self.delay_seconds = delay_seconds
        self.lock = threading.Lock()
        self.requests = 0
        self.head_requests = 0
        self.range_requests = 0
        self.bytes_sent = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                owner._serve(self, head=True)

            def do_GET(self):
                owner._serve(self, head=False)

            def log_message(self, *_args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def snapshot(self):
        with self.lock:
            return {
                "requests": self.requests,
                "head_requests": self.head_requests,
                "range_requests": self.range_requests,
                "bytes_sent": self.bytes_sent,
            }

    def _serve(self, handler: BaseHTTPRequestHandler, head: bool):
        relative = Path(unquote(urlsplit(handler.path).path).lstrip("/"))
        path = (self.root / relative).resolve()
        if self.root not in path.parents or not path.is_file():
            handler.send_error(404)
            return
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        header = handler.headers.get("Range")
        if header:
            try:
                unit, value = header.split("=", 1)
                if unit != "bytes" or "," in value:
                    raise ValueError
                left, right = value.split("-", 1)
                if left:
                    start = int(left)
                    end = int(right) if right else size - 1
                else:
                    length = int(right)
                    start = max(0, size - length)
                end = min(end, size - 1)
                if start < 0 or start > end:
                    raise ValueError
                partial = True
            except ValueError:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.end_headers()
                return
        length = end - start + 1
        with self.lock:
            self.requests += 1
            self.head_requests += int(head)
            self.range_requests += int(partial and not head)
        handler.send_response(206 if partial else 200)
        handler.send_header("Content-Type", "image/tiff")
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", str(length))
        if partial:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.end_headers()
        if head:
            return
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        sent = 0
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    sent += len(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self.lock:
                self.bytes_sent += sent


__all__ = ["RangeServer"]
