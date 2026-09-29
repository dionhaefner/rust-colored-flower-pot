"""RCFP Python API backed by the shared Rust core."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence
from urllib.parse import quote, urlencode

from ._version import __version__

__protocol_version__ = "0.10.1"

_PACKAGE_ROOT = Path(__file__).resolve().parent
_RUNTIME_ROOT = _PACKAGE_ROOT / "runtime"
if _RUNTIME_ROOT.is_dir():
    os.environ.setdefault("TERRACOTTA_RUNTIME_ROOT", str(_RUNTIME_ROOT))
    for variable, directory in (("GDAL_DATA", "gdal"), ("PROJ_DATA", "proj")):
        if (_RUNTIME_ROOT / "share" / directory).is_dir():
            os.environ.setdefault(variable, str(_RUNTIME_ROOT / "share" / directory))
    if os.name == "nt":
        _dll_directory = os.add_dll_directory(str(_RUNTIME_ROOT / "lib"))
    if os.name == "nt":
        pattern = "gdal*.dll"
    elif os.sys.platform == "darwin":
        pattern = "libgdal*.dylib"
    else:
        pattern = "libgdal*.so*"
    gdal_libraries = sorted((_RUNTIME_ROOT / "lib").glob(pattern))
    if gdal_libraries:
        os.environ.setdefault("TERRACOTTA_GDAL_LIBRARY", str(gdal_libraries[0]))

from . import _native


class RcfpError(RuntimeError):
    """An error returned by the native Terracotta adapter."""


def request(database: os.PathLike[str] | str, method: str, url: str):
    """Execute one HTTP-contract request without opening a socket.

    Returns ``(status, content_type, body, cacheable)`` where body is bytes.
    """

    return _native.request(os.fspath(database), method, url)


def render_preview(
    database: os.PathLike[str] | str,
    keys: Sequence[str],
    *,
    tile_size: Sequence[int] = (256, 256),
    stretch_range: Sequence[float | str | None] | None = None,
    colormap: str | None = None,
) -> bytes:
    """Render a single-band preview through the bounded Rust core."""

    path = "/singleband/" + "/".join(quote(str(key), safe="") for key in keys)
    path += "/preview.png"
    query: dict[str, str] = {"tile_size": json.dumps(list(tile_size))}
    if stretch_range is not None:
        query["stretch_range"] = json.dumps(list(stretch_range))
    if colormap is not None:
        query["colormap"] = colormap
    status, _content_type, body, _cacheable = request(
        database, "GET", f"{path}?{urlencode(query)}"
    )
    if status != 200:
        try:
            message = json.loads(body)["message"]
        except Exception:
            message = body.decode("utf-8", errors="replace")
        raise RcfpError(f"native render failed with HTTP {status}: {message}")
    return body


def identity(database: os.PathLike[str] | str) -> dict[str, str]:
    status, _content_type, body, _cacheable = request(
        database, "GET", "/__terracotta/identity"
    )
    if status != 200:
        raise RcfpError(f"identity request failed with HTTP {status}")
    return json.loads(body)


__all__ = [
    "RcfpError",
    "__version__",
    "__protocol_version__",
    "identity",
    "render_preview",
    "request",
]


def request_full(database, method, url, headers=None, body=b""):
    """Execute the HTTP contract including request/response headers and body."""
    result = _native.request_full(os.fspath(database), method, url,
                                 json.dumps({k.lower(): v for k, v in (headers or {}).items()}), body)
    return (*result[:4], json.loads(result[4]))


def data_operation(request):
    """Execute a structured native data operation, including atomic batches."""
    response = json.loads(_native.data_operation(json.dumps(request)))
    if "error" in response:
        raise RcfpError(response["error"])
    return response["ok"]


def compute_metadata(path, *, extra_metadata=None, use_chunks=None, max_shape=None):
    """Compute native full, sampled or chunked metadata."""
    result = data_operation(dict(operation="compute_metadata", path=os.fspath(path),
                                 options=dict(extra_metadata=extra_metadata,
                                              use_chunks=use_chunks, max_shape=max_shape)))
    result.pop("keys", None)
    result["bounds"] = tuple(result["bounds"])
    result["range"] = tuple(result["range"])
    return result


class DataStore:
    """Create, query, and update a raster catalog."""
    def __init__(self, path, *, provider=None):
        self.path = os.fspath(path)
        self.provider = provider

    def create(self, keys, *, key_descriptions=None):
        descriptions = key_descriptions or {}
        if set(descriptions) - set(keys):
            raise RcfpError("key description dict contains unknown keys")
        return data_operation(dict(operation="create", path=self.path, provider=self.provider,
                                   keys=[dict(key=k, description=descriptions.get(k, "")) for k in keys]))

    def insert(self, keys, path, *, override_path=None, metadata=None, skip_metadata=False):
        return data_operation(dict(operation="insert", path=self.path, provider=self.provider, keys=list(keys),
                                   raster=os.fspath(path), override_path=override_path,
                                   metadata=metadata, skip_metadata=skip_metadata))

    def delete(self, keys):
        return data_operation(dict(operation="delete", path=self.path, provider=self.provider, keys=list(keys)))

    def get_metadata(self, keys):
        value = data_operation(dict(operation="metadata", path=self.path, provider=self.provider, keys=list(keys)))
        if value is None:
            raise RcfpError("No dataset found")
        value.pop("keys", None)
        value["bounds"] = tuple(value["bounds"])
        value["range"] = tuple(value["range"])
        return value

    def batch(self, operations):
        """Atomically apply structured insert/delete operations, rolling back on error."""
        return data_operation(dict(operation="batch", path=self.path, provider=self.provider, operations=list(operations)))

__all__ += ["data_operation", "compute_metadata", "DataStore"]


class NativeApplication:
    """Own workers, remote snapshot leases and caches for an application lifetime."""
    def __init__(self, database, settings=None):
        self._session = _native.session_open(json.dumps(dict(kind="http", path=os.fspath(database), settings=settings or {})))

    def request(self, method, url, headers=None, body=b""):
        value = json.loads(_native.session_operation(self._session, json.dumps(dict(
            method=method, url=url, headers={k.lower(): v for k, v in (headers or {}).items()}, body=list(body)))))
        if "error" in value:
            raise RcfpError(value["error"])
        status, content_type, data, cache, headers = value["ok"]
        return status, content_type, bytes(data), cache, headers
