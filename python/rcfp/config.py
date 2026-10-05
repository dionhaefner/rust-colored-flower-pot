"""Terracotta settings represented as a named tuple and validated by the core."""

import json
import os
import warnings
from collections import namedtuple

from . import _native, exceptions
from ._profile import settings as profile_settings

_FIELDS = (
    "DRIVER_PATH",
    "DRIVER_PROVIDER",
    "DEBUG",
    "FLASK_PROFILE",
    "XRAY_PROFILE",
    "LOGLEVEL",
    "RASTER_CACHE_SIZE",
    "RASTER_CACHE_COMPRESS_LEVEL",
    "RASTER_AWS_ACCESS_KEY",
    "RASTER_AWS_SECRET_KEY",
    "RASTER_AWS_S3_ENDPOINT",
    "DEFAULT_TILE_SIZE",
    "LAZY_LOADING_MAX_SHAPE",
    "PNG_COMPRESS_LEVEL",
    "DB_CONNECTION_TIMEOUT",
    "REMOTE_DB_CACHE_DIR",
    "REMOTE_DB_CACHE_TTL",
    "RESAMPLING_METHOD",
    "REPROJECTION_METHOD",
    "ALLOWED_ORIGINS_METADATA",
    "ALLOWED_ORIGINS_TILES",
    "SQL_USER",
    "SQL_PASSWORD",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "POSTGRESQL_USER",
    "POSTGRESQL_PASSWORD",
    "USE_MULTIPROCESSING",
    "MAX_POST_METADATA_KEYS",
)


def _parse(config, *, defaults=False):
    result = json.loads(
        _native.data_operation(
            json.dumps(
                {
                    "operation": "default_settings" if defaults else "parse_settings",
                    "config": profile_settings(config),
                    "settings": profile_settings(),
                }
            )
        )
    )
    if "error" in result:
        raise ValueError(result["error"])
    values = result["ok"]
    values.pop("COMPAT_PROFILE", None)
    values.pop("EXPOSE_METRICS")
    for name in ("DEFAULT_TILE_SIZE", "LAZY_LOADING_MAX_SHAPE"):
        values[name] = tuple(values[name])
    return {name: values[name] for name in _FIELDS}


_defaults = _parse({}, defaults=True)
TerracottaSettings = namedtuple(
    "TerracottaSettings", _defaults, defaults=_defaults.values()
)
AVAILABLE_SETTINGS = TerracottaSettings._fields
DEPRECATION_MAP = {
    "MYSQL_USER": "SQL_USER",
    "MYSQL_PASSWORD": "SQL_PASSWORD",
    "POSTGRESQL_USER": "SQL_USER",
    "POSTGRESQL_PASSWORD": "SQL_PASSWORD",
}


def _parse_config(config=None):
    config = dict(config or {})
    for name in config:
        if name not in AVAILABLE_SETTINGS:
            raise ValueError(f"Could not parse configuration: unknown setting {name}")
    for name, replacement in DEPRECATION_MAP.items():
        value = config.get(name, os.environ.get(f"TC_{name}"))
        if value:
            warnings.warn(
                f"Setting TC_{name} is deprecated; use TC_{replacement} instead",
                exceptions.DeprecationWarning,
                stacklevel=2,
            )
    return TerracottaSettings(**_parse(config))
