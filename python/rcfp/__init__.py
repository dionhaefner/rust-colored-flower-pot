"""RCFP Python API backed by the shared Rust core."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from pkgutil import extend_path
from urllib.parse import quote, urlencode

from ._profile import PROFILE as compatibility_profile
from ._profile import settings as _profile_settings
from ._version import __version__

__protocol_version__ = "0.10.1"

__path__ = extend_path(__path__, __name__)

# Editable wheels load Python code from the checkout and native files from the wheel.
_NATIVE_SUFFIX = ".abi3.pyd" if os.name == "nt" else ".abi3.so"
_PACKAGE_ROOT = Path(__file__).resolve().parent
for _candidate in map(Path, __path__):
    if (_candidate / ("_native" + _NATIVE_SUFFIX)).is_file() and (
        _candidate / "runtime"
    ).is_dir():
        _PACKAGE_ROOT = _candidate
        break
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

    ``database`` accepts a catalog location or local source configuration.
    Returns ``(status, content_type, body, cacheable)`` where body is bytes.
    """

    return NativeApplication(database).request(method, url)[:4]


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
        except (KeyError, TypeError, ValueError):
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
    "__protocol_version__",
    "__version__",
    "identity",
    "render_preview",
    "request",
]


def request_full(database, method, url, headers=None, body=b""):
    """Execute the HTTP contract including request/response headers and body."""
    return NativeApplication(database).request(method, url, headers, body)


def data_operation(request):
    """Execute a structured native data operation, including atomic batches."""
    request = dict(request, settings=_profile_settings(request.get("settings")))
    response = json.loads(_native.data_operation(json.dumps(request)))
    if "error" in response:
        raise RcfpError(response["error"])
    return response["ok"]


def _metadata(value):
    if value is not None:
        value.pop("keys", None)
        value["bounds"] = tuple(value["bounds"])
        value["range"] = tuple(value["range"])
    return value


def compute_metadata(path, *, extra_metadata=None, use_chunks=None, max_shape=None):
    """Compute native full, sampled or chunked metadata."""
    result = data_operation(
        {
            "operation": "compute_metadata",
            "path": os.fspath(path),
            "options": {
                "extra_metadata": extra_metadata,
                "use_chunks": use_chunks,
                "max_shape": max_shape,
            },
        }
    )
    return _metadata(result)


class DataStore:
    """Create, query, and update a raster catalog."""

    def __init__(self, path, *, provider=None):
        self.path = os.fspath(path)
        self.provider = provider
        self._session = None

    def create(self, keys, *, key_descriptions=None, format="terracotta"):
        descriptions = key_descriptions or {}
        if set(descriptions) - set(keys):
            raise RcfpError("key description dict contains unknown keys")
        return data_operation(
            {
                "operation": "create",
                "path": self.path,
                "format": format,
                "provider": self.provider,
                "keys": [
                    {"key": k, "description": descriptions.get(k, "")} for k in keys
                ],
            }
        )

    def insert(
        self,
        keys,
        path,
        *,
        override_path=None,
        metadata=None,
        skip_metadata=False,
        band=None,
        values=None,
        stretch=None,
        colormap=None,
        revision=None,
    ):
        options = {}
        if band is not None:
            options["band"] = band
        if values is not None:
            options["values"] = values
        if stretch is not None or colormap is not None:
            options["style"] = {"stretch": stretch, "colormap": colormap}
        if revision is not None:
            options["revision"] = revision
        return data_operation(
            {
                **options,
                "operation": "insert",
                "path": self.path,
                "provider": self.provider,
                "keys": list(keys),
                "raster": os.fspath(path),
                "override_path": override_path,
                "metadata": metadata,
                "skip_metadata": skip_metadata,
            }
        )

    def define(self, keys, expression, bindings, *, stretch=None, colormap=None):
        """Persist a derived dataset using logical keyed inputs."""
        return data_operation(
            {
                "operation": "define",
                "path": self.path,
                "provider": self.provider,
                "keys": list(keys),
                "expression": expression,
                "bindings": {name: list(values) for name, values in bindings.items()},
                "style": {"stretch": stretch, "colormap": colormap},
            }
        )

    def ingest(
        self,
        source,
        *,
        search=None,
        options=None,
        key_mapping=None,
        skip_metadata=False,
        stretch=None,
        colormap=None,
    ):
        """Import a STAC snapshot and keyed bindings into this catalog."""
        return data_operation(
            {
                "operation": "ingest_source",
                "path": self.path,
                "provider": self.provider,
                "source": os.fspath(source),
                "search": search,
                "options": options or {},
                "key_mapping": key_mapping or {},
                "skip_metadata": skip_metadata,
                "style": {"stretch": stretch, "colormap": colormap},
            }
        )

    def get_keys(self):
        return data_operation(
            {"operation": "keys", "path": self.path, "provider": self.provider}
        )

    def get_datasets(self, *, where=None):
        return data_operation(
            {
                "operation": "datasets",
                "path": self.path,
                "provider": self.provider,
                "where": where or {},
            }
        )

    def delete(self, keys):
        return data_operation(
            {
                "operation": "delete",
                "path": self.path,
                "provider": self.provider,
                "keys": list(keys),
            }
        )

    def get_metadata(self, keys):
        value = data_operation(
            {
                "operation": "metadata",
                "path": self.path,
                "provider": self.provider,
                "keys": list(keys),
            }
        )
        if value is None:
            raise RcfpError("No dataset found")
        return _metadata(value)

    def get_raster_tile(
        self,
        keys,
        *,
        tile_bounds=None,
        tile_size=(256, 256),
        preserve_values=False,
        asynchronous=False,
    ):
        """Read logical samples on a Web Mercator grid as a masked NumPy array."""
        from ._bridge import Session, submit

        if self._session is None:
            self._session = Session("catalog", self.path, self.provider)
        request = {
            "keys": keys,
            "tile_bounds": tile_bounds,
            "tile_size": list(tile_size),
            "preserve_values": preserve_values,
        }
        return (
            submit(self._session.raster, **request)
            if asynchronous
            else self._session.raster(**request)
        )

    def batch(self, operations):
        """Atomically apply structured insert/delete operations, rolling back on error."""
        return data_operation(
            {
                "operation": "batch",
                "path": self.path,
                "provider": self.provider,
                "operations": list(operations),
            }
        )


__all__ += ["DataStore", "compute_metadata", "data_operation"]


class NativeApplication:
    """Serve a catalog with persistent workers, source leases and caches.

    ``database`` accepts SQL catalogs, STAC documents and local source
    configuration files. All use the same request interface.
    """

    def __init__(self, database, settings=None):
        self._session = _native.session_open(
            json.dumps(
                {
                    "kind": "http",
                    "path": os.fspath(database),
                    "settings": _profile_settings(settings),
                }
            )
        )

    def request(self, method, url, headers=None, body=b""):
        status, content_type, data, cache, response_headers = (
            _native.session_request_full(
                self._session,
                method,
                url,
                json.dumps({k.lower(): v for k, v in (headers or {}).items()}),
                body,
            )
        )
        return status, content_type, data, cache, json.loads(response_headers)


__all__ += ["NativeApplication", "compatibility_profile", "request_full"]

if compatibility_profile == "terracotta-0.10.1":
    from .config import _parse_config

    _settings = _parse_config()
    _overwritten_settings = set()

    def get_settings():
        """Return the current Terracotta-compatible settings."""
        return _settings

    def update_settings(**new_config):
        """Override public Terracotta settings without changing the profile."""
        global _settings, _overwritten_settings
        current = {key: getattr(_settings, key) for key in _overwritten_settings}
        _settings = _parse_config({**current, **new_config})
        _overwritten_settings |= new_config.keys()

    from .drivers import get_driver

    __all__ += ["get_driver", "get_settings", "update_settings"]

from .layers import LayerApplication, LayerStore, inspect_asset

__all__ += ["LayerApplication", "LayerStore", "inspect_asset"]

from .compute import ComputeApplication, ComputeContext

__all__ += ["ComputeApplication", "ComputeContext"]
