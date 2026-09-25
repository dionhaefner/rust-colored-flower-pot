"""Typed legacy boundary over the shared native implementation."""
import json
from terracotta_rust import _native
from terracotta import exceptions, get_settings


def settings():
    import terracotta
    return dict(get_settings()._asdict(), PYTHON_PROTOCOL_VERSION=terracotta.__version__)


def json_value(value):
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, 'tolist'):
        return value.tolist()
    raise TypeError(f'{type(value).__name__} is not JSON serializable')


def translate(message):
    if "error while reading file" in message:
        return IOError(message)
    if "does not contain any valid data" in message:
        return ValueError(message)
    if "not writable" in message or "readonly" in message:
        return exceptions.DatabaseNotWritableError(message)
    if "No dataset" in message:
        return exceptions.DatasetNotFoundError(message)
    if "key" in message and any(s in message for s in ("unrecognized", "alphanumeric", "reserved", "unknown", "number")):
        return exceptions.InvalidKeyError(message)
    if "out of bounds" in message.lower():
        return exceptions.TileOutOfBoundsError(message)
    if any(s in message for s in ("max_shape", "use_chunks", "dimensions")):
        return ValueError(message)
    return exceptions.InvalidDatabaseError(message)


def decode(raw):
    value = json.loads(raw)
    if "error" in value:
        raise translate(value["error"])
    return value["ok"]


def operation(**request):
    request["settings"] = settings()
    return decode(_native.data_operation(json.dumps(request, default=json_value)))


class Session:
    def __init__(self, kind, path="", provider=None, config=None):
        try:
            self._handle = _native.session_open(json.dumps(dict(kind=kind, path=str(path), provider=provider,
                                                                settings=settings() if config is None else config)))
        except RuntimeError as error:
            raise translate(str(error)) from error

    def call(self, **request):
        return decode(_native.session_operation(self._handle, json.dumps(request, default=json_value)))


def metadata(value):
    if value is None:
        return None
    value.pop("keys", None)
    value["bounds"] = tuple(value["bounds"])
    value["range"] = tuple(value["range"])
    return value
