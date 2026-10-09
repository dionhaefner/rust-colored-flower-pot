"""Trusted Python scalar layers hosted over the shared native raster application."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from . import NativeApplication, RcfpError, _native, compatibility_profile

_STYLE = {"stretch_range", "colormap", "explicit_color_map", "color_transform"}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(map(_freeze, value))
    return value


@dataclass(frozen=True)
class ComputeContext:
    """Immutable requested grid, one observation, calibration and model dependencies."""

    grid: object
    observation: object
    inputs: object
    source_revision: str
    parameters: object
    dependency_revisions: object


class _CallbackFailure(RuntimeError):
    pass


def _keys(name, definition):
    return [name, definition["observation"], "value"]


def _tileset(name, definition):
    return (
        "k-"
        + json.dumps(_keys(name, definition), ensure_ascii=False, separators=(",", ":"))
        .encode()
        .hex()
    )


class _Cache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.bytes = 0
        self.items = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key):
        item = self.items.get(key)
        if item is not None:
            self.hits += 1
            self.items.move_to_end(key)
            return item[0]
        self.misses += 1
        return None

    def put(self, key, value, size):
        if size > self.capacity:
            return
        old = self.items.pop(key, None)
        if old:
            self.bytes -= old[1]
        while self.bytes + size > self.capacity and self.items:
            _, (_, removed) = self.items.popitem(last=False)
            self.bytes -= removed
            self.evictions += 1
        self.items[key] = (value, size)
        self.bytes += size


class ComputeApplication(NativeApplication):
    """Serve scalar callbacks over persisted LayerStore observations.

    Host threads run callbacks after native resampling. Use a context manager
    or call close() to drain admitted work.
    """

    def __init__(
        self,
        database,
        settings=None,
        *,
        workers=2,
        queue_capacity=8,
        timeout=30.0,
        compute_cache_bytes=64 * 1024 * 1024,
        image_cache_bytes=16 * 1024 * 1024,
    ):
        if compatibility_profile != "native":
            raise ValueError("Python compute layers require the native profile")
        for name, value, low, high in (
            ("workers", workers, 1, 64),
            ("queue_capacity", queue_capacity, 0, 1024),
            ("compute_cache_bytes", compute_cache_bytes, 0, 512 * 1024 * 1024),
            ("image_cache_bytes", image_cache_bytes, 0, 512 * 1024 * 1024),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"invalid {name}")
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError("timeout must be between zero and 3600 seconds")
        super().__init__(database, settings)
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="rcfp-compute")
        self._slots = threading.BoundedSemaphore(workers + queue_capacity)
        self._lock = threading.RLock()
        self._layers = {}
        self._flights = {}
        self._computed = _Cache(compute_cache_bytes)
        self._images = _Cache(image_cache_bytes)
        self._timeout = timeout
        self._closed = False
        self._admitted = 0
        self._callbacks = 0

    def _operation(self, **request):
        result = json.loads(_native.session_operation(self._session, _json(request)))
        if "error" in result:
            raise ValueError(result["error"])
        return result["ok"]

    def _binary(self, request, body=b""):
        return _native.session_compute(self._session, _json(request), body)[2]

    def register(
        self,
        layer_id,
        callback,
        *,
        source,
        observation,
        inputs,
        version,
        preview_grid,
        parameters=None,
        dependency_revisions=None,
        display=None,
    ):
        """Publish a trusted callback(values, valid, context) returning (values, valid).

        Each input declares asset, one-based band/name, and values='raw'/'scaled'.
        Parameters declare type, default and optional minimum/maximum or enum.
        dependency_revisions={} declares no external dependencies, while None
        disables compute and image retention. Change version when code changes.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("compute application is closed")
        if not isinstance(layer_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", layer_id
        ):
            raise ValueError(
                "layer ID requires 1 to 128 letters, digits, dots, dashes or underscores"
            )
        if (
            not callable(callback)
            or inspect.iscoroutinefunction(callback)
            or not isinstance(version, str)
            or not version
        ):
            raise ValueError(
                "a synchronous callback and nonempty compute version are required"
            )
        definition = copy.deepcopy(
            {
                "id": layer_id,
                "source": source,
                "observation": observation,
                "inputs": inputs,
                "version": version,
                "preview_grid": preview_grid,
                "parameters": parameters or {},
                "dependency_revisions": dependency_revisions,
                "display": display or {"stretch_range": [0, 1]},
            }
        )
        _json(definition)
        if not isinstance(observation, str) or not observation:
            raise ValueError("a nonempty observation ID is required")
        if not set(definition["display"]) <= _STYLE:
            raise ValueError("unsupported display setting")
        if "stretch_range" not in definition["display"]:
            definition["display"]["stretch_range"] = [0, 1]
        if dependency_revisions is not None and (
            not isinstance(dependency_revisions, dict)
            or any(
                not isinstance(k, str) or not k or not isinstance(v, str) or not v
                for k, v in dependency_revisions.items()
            )
        ):
            raise ValueError(
                "dependency revisions require nonempty names and revision strings"
            )
        for name, schema in definition["parameters"].items():
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)
                or name in _STYLE
                or name in {"revision", "tile_size"}
            ):
                raise ValueError("invalid compute parameter name")
            if (
                not isinstance(schema, dict)
                or not set(schema) <= {"type", "default", "minimum", "maximum", "enum"}
                or "default" not in schema
            ):
                raise ValueError("parameters require a type and default")
            self._parameter(schema, schema["default"])
        prepared = self._prepare(definition, preview_grid)
        # Validate display defaults before publishing the layer.
        self._operation(operation="compute_style", style=self._style(definition, {}))
        if prepared["context"]["grid"] != preview_grid:
            raise ValueError("invalid preview grid")
        if preview_grid["crs"] != "EPSG:3857":
            raise ValueError("preview_grid must use EPSG:3857")
        west, south, east, north = preview_grid["bounds"]
        definition["bounds"] = [
            west / 6378137 * 180 / math.pi,
            math.degrees(math.atan(math.sinh(south / 6378137))),
            east / 6378137 * 180 / math.pi,
            math.degrees(math.atan(math.sinh(north / 6378137))),
        ]
        if super().request("GET", "/layers/" + layer_id)[0] == 200:
            raise ValueError("compute layer ID conflicts with a persisted layer")
        with self._lock:
            if self._closed:
                raise RuntimeError("compute application is closed")
            if len(self._layers) >= 256 and layer_id not in self._layers:
                raise ValueError("compute application supports at most 256 layers")
            self._layers[layer_id] = (callback, definition)

    @staticmethod
    def _parameter(schema, value):
        kind = schema.get("type")
        types = {
            "number": (int, float),
            "integer": (int,),
            "boolean": (bool,),
            "string": (str,),
        }
        if kind not in types or type(value) not in types[kind]:
            raise ValueError("compute parameter has the wrong type")
        if kind in {"number", "integer"} and (
            not math.isfinite(value)
            or value < schema.get("minimum", -math.inf)
            or value > schema.get("maximum", math.inf)
        ):
            raise ValueError("compute parameter is outside its bounds")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("compute parameter is outside its enum")
        return value

    def _prepare(self, definition, grid):
        return self._operation(
            operation="compute_prepare",
            source=definition["source"],
            observation=definition["observation"],
            inputs=definition["inputs"],
            grid=grid,
        )

    @staticmethod
    def _style(definition, query):
        style = dict(definition["display"])
        style.update({k: v for k, v in query.items() if k in _STYLE})
        return {k: v if isinstance(v, str) else _json(v) for k, v in style.items()}

    def _compute(self, callback, definition, plan, parameters, deadline):
        import numpy as np

        identity = _json(
            [
                definition["id"],
                plan["identity"],
                definition["version"],
                definition["inputs"],
                parameters,
                definition["dependency_revisions"],
            ]
        )
        retain = plan["cacheable"] and definition["dependency_revisions"] is not None
        # Undeclared dependencies also disable sharing concurrent callback results.
        with self._lock:
            cached = self._computed.get(identity) if retain else None
            if cached is not None:
                return identity, cached, retain
            flight = self._flights.get(identity) if retain else None
            leader = flight is None
            if leader:
                flight = Future()
                if retain:
                    self._flights[identity] = flight
        if not leader:
            return (
                identity,
                flight.result(timeout=max(0, deadline - time.monotonic())),
                retain,
            )
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            payload = self._binary({"operation": "inputs", "plan": plan})
            shape = tuple(plan["grid"]["size"])
            count = math.prod(shape)
            values, valid = {}, {}
            for index, name in enumerate(sorted(plan["inputs"])):
                offset = index * count * 9
                values[name] = np.frombuffer(
                    payload, dtype="<f8", count=count, offset=offset
                ).reshape(shape)
                valid[name] = np.frombuffer(
                    payload, dtype=np.bool_, count=count, offset=offset + count * 8
                ).reshape(shape)
            if time.monotonic() >= deadline:
                raise TimeoutError()
            context = plan["context"]
            context = ComputeContext(
                **{
                    k: _freeze(context[k])
                    for k in ("grid", "observation", "inputs", "source_revision")
                },
                parameters=_freeze(parameters),
                dependency_revisions=_freeze(definition["dependency_revisions"]),
            )
            try:
                with self._lock:
                    self._callbacks += 1
                output = callback(
                    MappingProxyType(values), MappingProxyType(valid), context
                )
                if not isinstance(output, tuple) or len(output) != 2:
                    raise ValueError("callback must return a (values, valid) tuple")
                data, mask = map(np.asarray, output)
                if (
                    data.shape != shape
                    or mask.shape != shape
                    or data.dtype.kind not in "iuf"
                    or mask.dtype != np.bool_
                ):
                    raise ValueError(
                        "callback output requires numeric values and a boolean validity mask on the supplied grid"
                    )
                data = np.array(data, dtype="<f8", order="C", copy=True)
                mask = np.array(mask, dtype=np.bool_, order="C", copy=True)
                if not np.isfinite(data[mask]).all():
                    raise ValueError("valid callback values must be finite")
                buffers = data.tobytes() + mask.tobytes()
            except BaseException as error:
                raise _CallbackFailure("compute callback failed") from error
            # Recheck the source before publishing or retaining a completed model result.
            current = self._prepare(definition, plan["grid"])
            if current["identity"] != plan["identity"]:
                raise RcfpError("source changed during computation; retry")
            with self._lock:
                if retain:
                    self._computed.put(identity, buffers, len(buffers))
            flight.set_result(buffers)
            return identity, buffers, retain
        except BaseException as error:
            flight.set_exception(error)
            raise
        finally:
            with self._lock:
                if retain:
                    self._flights.pop(identity, None)

    def _render(self, layer, grid, query, deadline):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        callback, definition = layer
        parameters = {
            name: self._parameter(
                schema,
                (query[name] if schema["type"] == "string" else json.loads(query[name]))
                if name in query
                else schema["default"],
            )
            for name, schema in definition["parameters"].items()
        }
        style = self._style(definition, query)
        self._operation(operation="compute_style", style=style)
        plan = self._prepare(definition, grid)
        identity, buffers, retain = self._compute(
            callback, definition, plan, parameters, deadline
        )
        image_key = _json([identity, style])
        with self._lock:
            image = self._images.get(image_key) if retain else None
        if image is None:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            image = self._binary(
                {"operation": "render", "size": grid["size"], "style": style}, buffers
            )
            with self._lock:
                if retain:
                    self._images.put(image_key, image, len(image))
        revision = hashlib.sha256(
            (image_key + hashlib.sha256(image).hexdigest()).encode()
        ).hexdigest()
        return image, revision

    @staticmethod
    def _response(status, content_type, body, method, headers=None):
        extra = {
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, If-None-Match",
            "Access-Control-Expose-Headers": "ETag, X-RCFP-Revision",
        }
        extra.update(headers or {})
        if method == "HEAD" and status != 304:
            extra["Content-Length"] = str(len(body))
            body = b""
        return status, content_type, body, False, extra

    def _discover(self, path, layers, query, headers, method):
        page, limit = int(query.get("page", 0)), int(query.get("limit", 100))
        if page < 0 or not 1 <= limit <= 1000 or page * limit > 2**63 - 1:
            raise ValueError("invalid discovery page or limit")
        result_key = "tilesets" if path == "/tilesets" else "datasets"
        validate = super().request(
            "GET", path + "?" + urlencode(dict(query, page=0, limit=1)), headers
        )
        if validate[0] != 200:
            return validate
        base = (
            self._operation(operation="compute_url", headers=headers, absolute=False)
            if result_key == "tilesets"
            else ""
        )
        computed = []
        for name, (_, definition) in sorted(layers.items()):
            keys = _keys(name, definition)
            if result_key == "tilesets":
                identity = _tileset(name, definition)
                computed.append(
                    {
                        "id": identity,
                        "keys": keys,
                        "title": name,
                        "compute": {
                            "version": definition["version"],
                            "observation": definition["observation"],
                            "parameters": definition["parameters"],
                        },
                        "tilejson": base + "/tilejson/" + identity + ".json",
                        "tileMatrixSets": [
                            {
                                "id": "WebMercatorQuad",
                                "definition": base + "/tileMatrixSets/WebMercatorQuad",
                                "tiles": base
                                + "/tiles/"
                                + identity
                                + "/WebMercatorQuad/{z}/{x}/{y}.png",
                            }
                        ],
                    }
                )
            else:
                row = dict(zip(("layer", "observation", "channel"), keys))
                if all(
                    key not in query
                    or row[key]
                    in (
                        query[key][1:-1].split(",")
                        if query[key].startswith("[") and query[key].endswith("]")
                        else [query[key]]
                    )
                    for key in row
                ):
                    computed.append(row)
        if result_key == "datasets":
            order = query.get("order_by", "[layer,observation,channel]")
            order = order[1:-1].split(",") if order.startswith("[") else [order]
            ascending = query.get("ascending", "true").lower() in {
                "true",
                "1",
                "yes",
                "on",
                "t",
                "y",
            }
            computed.sort(
                key=lambda row: tuple(row.get(key, "") for key in order),
                reverse=not ascending,
            )
        offset = page * limit
        descriptors = computed[offset : offset + limit]
        native_offset = max(0, offset - len(computed))
        if len(descriptors) < limit:
            native_page = native_offset // limit

            def fetch(current_page):
                return super(ComputeApplication, self).request(
                    "GET",
                    path + "?" + urlencode(dict(query, page=current_page, limit=limit)),
                    headers,
                )

            native = fetch(native_page)
            if native[0] != 200:
                return native
            rows = json.loads(native[2])[result_key]
            skip = native_offset % limit
            descriptors += rows[skip : skip + limit - len(descriptors)]
            if len(descriptors) < limit and len(rows) == limit:
                native = fetch(native_page + 1)
                if native[0] != 200:
                    return native
                descriptors += json.loads(native[2])[result_key][
                    : limit - len(descriptors)
                ]
        return self._response(
            200,
            "application/json",
            _json({result_key: descriptors, "page": page, "limit": limit}).encode(),
            method,
        )

    def _swagger(self, url, layers, headers, method):
        native = super().request("GET", url, headers)
        if native[0] != 200:
            return native
        spec = json.loads(native[2])
        schemas = spec["components"]["schemas"]
        declarations = {
            _tileset(name, definition): {
                "keys": _keys(name, definition),
                "version": definition["version"],
                "observation": definition["observation"],
                "parameters": definition["parameters"],
            }
            for name, (_, definition) in layers.items()
        }
        spec["x-rcfp-python-compute"] = declarations
        schemas["PythonComputeDeclaration"] = {
            "type": "object",
            "required": ["version", "observation", "parameters"],
            "properties": {
                "version": {"type": "string"},
                "observation": {"type": "string"},
                "parameters": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                },
            },
        }
        tilejson = {
            "type": "object",
            "required": [
                "tilejson",
                "name",
                "tiles",
                "bounds",
                "scheme",
                "tileSize",
                "rcfp",
            ],
            "properties": {
                "tilejson": {"type": "string", "enum": ["3.0.0"]},
                "name": {"type": "string"},
                "tiles": {"type": "array", "items": {"type": "string"}},
                "bounds": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {"type": "number"},
                },
                "scheme": {"type": "string", "enum": ["xyz"]},
                "tileSize": {"type": "integer", "enum": [256]},
                "rcfp": {
                    "type": "object",
                    "required": ["keys", "tileMatrixSet", "defaults", "compute"],
                    "properties": {
                        "compute": {
                            "$ref": "#/components/schemas/PythonComputeDeclaration"
                        }
                    },
                },
            },
        }
        schemas["TileJSON"] = {"anyOf": [schemas["TileJSON"], tilejson]}
        item = schemas["Tilesets"]["properties"]["tilesets"]["items"]
        computed = copy.deepcopy(item)
        computed["required"] = [
            key for key in computed["required"] if key != "point"
        ] + ["compute"]
        computed["properties"]["compute"] = {
            "$ref": "#/components/schemas/PythonComputeDeclaration"
        }
        schemas["Tilesets"]["properties"]["tilesets"]["items"] = {
            "anyOf": [item, computed]
        }
        schemas["Metadata"] = {
            "anyOf": [
                schemas["Metadata"],
                {
                    "type": "object",
                    "required": ["keys", "bounds", "range", "metadata"],
                    "properties": {
                        "keys": {"type": "object"},
                        "bounds": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {"type": "number"},
                        },
                        "range": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"type": "number"},
                        },
                        "metadata": {
                            "type": "object",
                            "required": ["compute_version", "defaults"],
                        },
                    },
                },
            ]
        }
        return self._response(200, "application/json", _json(spec).encode(), method)

    def request(self, method, url, headers=None, body=b""):
        parsed = urlsplit(url)
        parts = parsed.path.strip("/").split("/")
        method = method.upper()
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        with self._lock:
            layers = dict(self._layers)
            closed = self._closed

        def response(status, data, extra=None):
            return self._response(
                status, "application/json", _json(data).encode(), method, extra
            )

        if closed:
            return response(503, {"error": "compute application is closed"})
        if parsed.path == "/swagger.json" and method in {"GET", "HEAD"}:
            return self._swagger(url, layers, headers, method)
        compute_id = None
        if (
            len(parts) > 1
            and parts[0] in {"tilejson", "tiles"}
            and parts[1].startswith("k-")
        ):
            encoded = parts[1].removesuffix(".json")
            try:
                keys = json.loads(bytes.fromhex(encoded[2:]))
                for name, (_, definition) in layers.items():
                    if keys == _keys(name, definition):
                        compute_id = name
                        break
            except (ValueError, UnicodeDecodeError):
                pass
        elif parts[0] in {"singleband", "metadata"} and len(parts) >= 4:
            keys = [unquote(p) for p in parts[1:4]]
            for name, (_, definition) in layers.items():
                if keys == _keys(name, definition):
                    compute_id = name
                    break
        discovery = parsed.path in {"/tilesets", "/datasets"}
        if not discovery and compute_id is None:
            return super().request(method, url, headers, body)
        if method == "OPTIONS":
            return self._response(204, "application/json", b"", method)
        if method not in {"GET", "HEAD"}:
            return response(
                405, {"error": "method not allowed"}, {"Allow": "GET, HEAD, OPTIONS"}
            )
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
            if len(parsed.query) > 16384 or len(dict(pairs)) != len(pairs):
                raise ValueError("query is too large or contains duplicate parameters")
            query = dict(pairs)
            if discovery:
                return self._discover(parsed.path, layers, query, headers, method)
            layer = layers[compute_id]
            definition = layer[1]
            if not set(query) <= _STYLE | set(definition["parameters"]) | {
                "revision",
                "tile_size",
            }:
                raise ValueError("unsupported compute or display parameter")
            if parts[0] == "tilejson" and len(parts) == 2:
                base = self._operation(
                    operation="compute_url", headers=headers, absolute=True
                )
                template = (
                    base
                    + "/tiles/"
                    + _tileset(compute_id, definition)
                    + "/WebMercatorQuad/{z}/{x}/{y}.png"
                )
                if query:
                    template += "?" + urlencode(query)
                return response(
                    200,
                    {
                        "tilejson": "3.0.0",
                        "name": compute_id,
                        "tiles": [template],
                        "bounds": definition["bounds"],
                        "scheme": "xyz",
                        "minzoom": 0,
                        "maxzoom": 22,
                        "tileSize": 256,
                        "attribution": "",
                        "rcfp": {
                            "keys": _keys(compute_id, definition),
                            "tileMatrixSet": "WebMercatorQuad",
                            "defaults": definition["display"],
                            "compute": {
                                "version": definition["version"],
                                "parameters": definition["parameters"],
                                "observation": definition["observation"],
                            },
                        },
                    },
                )
            if parts[0] == "metadata" and len(parts) == 4:
                return response(
                    200,
                    {
                        "keys": dict(
                            zip(
                                ("layer", "observation", "channel"),
                                _keys(compute_id, definition),
                            )
                        ),
                        "bounds": definition["bounds"],
                        "range": definition["display"]["stretch_range"],
                        "metadata": {
                            "defaults": definition["display"],
                            "compute_version": definition["version"],
                        },
                    },
                )
            grid = copy.deepcopy(definition["preview_grid"])
            if (
                parts[0] == "singleband"
                and len(parts) == 5
                and parts[4] == "preview.png"
            ):
                if "tile_size" in query:
                    grid["size"] = json.loads(query["tile_size"])
            elif (parts[0] == "singleband" and len(parts) == 7) or (
                parts[0] == "tiles"
                and len(parts) == 6
                and parts[2] == "WebMercatorQuad"
            ):
                if "tile_size" in query:
                    raise ValueError("map tiles have fixed 256 by 256 dimensions")
                coords = parts[-3:]
                if not coords[-1].endswith(".png"):
                    return response(404, {"error": "route not found"})
                coords[-1] = coords[-1][:-4]
                grid = self._operation(
                    operation="compute_grid",
                    matrix="WebMercatorQuad",
                    coordinates=[int(v) for v in coords],
                    resampling=grid["resampling"],
                )
            else:
                return response(404, {"error": "route not found"})
            if len(body) > 1024 * 1024:
                return response(413, {"error": "request body exceeds 1 MiB"})
            deadline = time.monotonic() + self._timeout
            with self._lock:
                if self._closed or not self._slots.acquire(blocking=False):
                    return response(503, {"error": "compute capacity is full"})
                self._admitted += 1
                try:
                    future = self._pool.submit(
                        self._render, layer, grid, query, deadline
                    )
                except BaseException:
                    self._admitted -= 1
                    self._slots.release()
                    raise
                future.add_done_callback(self._finished)
            try:
                image, revision = future.result(
                    timeout=max(0, deadline - time.monotonic())
                )
            except CancelledError:
                return response(503, {"error": "compute application is closing"})
            except TimeoutError:
                return response(
                    504, {"error": "compute deadline elapsed; active work continues"}
                )
            if "revision" in query and query["revision"] != revision:
                return response(
                    409, {"error": "render revision is no longer available"}
                )
            extra = {"ETag": '"' + revision + '"', "X-RCFP-Revision": revision}
            if headers.get("if-none-match") in {extra["ETag"], "*"}:
                return self._response(304, "image/png", b"", method, extra)
            return self._response(200, "image/png", image, method, extra)
        except (ValueError, TypeError, KeyError) as error:
            return response(400, {"error": str(error)})
        except RuntimeError as error:
            message = str(error)
            if message == "raster execution queue is full":
                return response(503, {"error": "raster capacity is full"})
            if message == "raster execution timed out while native work continues":
                return response(
                    504, {"error": "raster deadline elapsed; active work continues"}
                )
            if message.startswith("source changed"):
                return response(
                    409, {"error": "source changed during computation; retry"}
                )
            return response(500, {"error": "compute layer failed"})
        except Exception:  # noqa: BLE001 - exceptions may contain credentials or private inputs
            return response(500, {"error": "compute layer failed"})

    def _finished(self, _future):
        with self._lock:
            self._admitted -= 1
            self._slots.release()

    def compute_metrics(self):
        """Return bounded host admission and independent cache counters."""
        with self._lock:

            def cache_metrics(cache):
                return {
                    "bytes": cache.bytes,
                    "entries": len(cache.items),
                    "hits": cache.hits,
                    "misses": cache.misses,
                    "evictions": cache.evictions,
                }

            return {
                "admitted": self._admitted,
                "callbacks": self._callbacks,
                "computed": cache_metrics(self._computed),
                "images": cache_metrics(self._images),
                "closed": self._closed,
            }

    def close(self):
        """Stop admission, cancel queued work, and join running callbacks and reads."""
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._session = None
            self._computed.items.clear()
            self._computed.bytes = 0
            self._images.items.clear()
            self._images.bytes = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
