"""Small WSGI boundary backed by the RCFP native application."""

from __future__ import annotations

import os
from http import HTTPStatus
from urllib.parse import quote

from . import NativeApplication


class Application:
    """Host a catalog location or local source configuration through WSGI."""

    def __init__(self, database: str | None = None, settings=None, *, application=None):
        if application is not None:
            self.database = database
            self.native = application
            return
        self.database = database or os.environ.get("TC_DRIVER_PATH")
        if not self.database:
            raise RuntimeError("WSGI adapter requires a source or TC_DRIVER_PATH")
        self.native = NativeApplication(self.database, settings)

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = quote(
            environ.get("PATH_INFO", "/").encode("latin-1").decode("utf-8"), safe="/"
        )
        query = environ.get("QUERY_STRING", "")
        url = f"{path}?{query}" if query else path
        request_headers = {
            k[5:].lower().replace("_", "-"): v
            for k, v in environ.items()
            if k.startswith("HTTP_")
        }
        if environ.get("CONTENT_TYPE"):
            request_headers["content-type"] = environ["CONTENT_TYPE"]
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = -1
        if length < 0:
            start_response(
                "400 Bad Request",
                [("Content-Type", "text/plain"), ("Content-Length", "0")],
            )
            return [b""]
        body = (
            environ["wsgi.input"].read(min(length, 1024 * 1024 + 1)) if length else b""
        )
        status, content_type, body, _cacheable, extra_headers = self.native.request(
            method, url, request_headers, body
        )
        phrase = (
            HTTPStatus(status).phrase
            if status in HTTPStatus._value2member_map_
            else "Unknown"
        )
        headers = [("Content-Type", content_type), ("Content-Length", str(len(body)))]
        if status == 304:
            headers = [(k, v) for k, v in headers if k != "Content-Length"]
        if "Content-Length" in extra_headers:
            headers = [(k, v) for k, v in headers if k != "Content-Length"]
        headers.extend(extra_headers.items())
        start_response(f"{status} {phrase}", headers)
        return [body]


def create_app(database: str | None = None) -> Application:
    return Application(database)


app = Application() if os.environ.get("TC_DRIVER_PATH") else None
