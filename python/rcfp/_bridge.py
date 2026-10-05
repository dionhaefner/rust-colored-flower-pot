"""Typed Python representation boundary over the shared Rust core."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from . import _native, exceptions
from ._profile import settings as profile_settings


def settings():
    from . import get_settings

    return profile_settings(get_settings()._asdict())


def json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def translate(message):
    if "database does not exist" in message:
        return exceptions.InvalidDatabaseError(
            message + "; make sure you ran driver.create()"
        )
    if "error while reading" in message and "file" in message:
        return OSError(message)
    if "does not contain any valid data" in message:
        return ValueError(message)
    if (
        "not writable" in message
        or "readonly" in message
        or "requires a writable" in message
    ):
        return exceptions.DatabaseNotWritableError(message)
    if "No dataset" in message:
        return exceptions.DatasetNotFoundError(message)
    if "key" in message and any(
        word in message
        for word in (
            "unrecognized",
            "alphanumeric",
            "reserved",
            "unknown",
            "number",
            "type",
            "cannot be",
        )
    ):
        return exceptions.InvalidKeyError(message)
    if "out of bounds" in message.lower():
        return exceptions.TileOutOfBoundsError(message)
    if any(
        word in message
        for word in (
            "max_shape",
            "use_chunks",
            "dimensions",
            "page and limit",
            "invalid length",
        )
    ):
        return ValueError(message)
    return exceptions.InvalidDatabaseError(message)


def decode(raw):
    value = json.loads(raw)
    if "error" in value:
        raise translate(value["error"])
    return value["ok"]


def operation(*, config=None, **request):
    request["settings"] = settings() if config is None else profile_settings(config)
    return decode(_native.data_operation(json.dumps(request, default=json_value)))


class Session:
    def __init__(self, kind, path="", provider=None, config=None):
        try:
            self._handle = _native.session_open(
                json.dumps(
                    {
                        "kind": kind,
                        "path": str(path),
                        "provider": provider,
                        "settings": settings()
                        if config is None
                        else profile_settings(config),
                    }
                )
            )
        except RuntimeError as error:
            raise translate(str(error)) from error

    def call(self, **request):
        return decode(
            _native.session_operation(
                self._handle, json.dumps(request, default=json_value)
            )
        )

    def raster(self, **request):
        import numpy as np

        try:
            dtype, shape, values, mask = _native.session_raster(
                self._handle, json.dumps(request, default=json_value)
            )
        except RuntimeError as error:
            raise translate(str(error)) from error
        dtypes = {
            1: "uint8",
            2: "uint16",
            3: "int16",
            4: "uint32",
            5: "int32",
            6: "float32",
            7: "float64",
            12: "uint64",
            13: "int64",
            14: "int8",
        }
        values = np.frombuffer(values, dtype="=f8").astype(dtypes[dtype]).reshape(shape)
        mask = np.frombuffer(mask, dtype=bool).reshape(shape).copy()
        return np.ma.array(values, mask=mask)


def metadata(value):
    if value is not None:
        value.pop("keys", None)
        value["bounds"] = tuple(value["bounds"])
        value["range"] = tuple(value["range"])
    return value


_executor = None


def submit(call, **request):
    global _executor
    if _executor is None:
        from . import get_settings

        _executor = ThreadPoolExecutor(
            max_workers=3 if get_settings().USE_MULTIPROCESSING else 1
        )
    return _executor.submit(call, **request)
