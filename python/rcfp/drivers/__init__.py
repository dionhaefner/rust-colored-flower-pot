"""Driver entry points with native provider parsing and catalog identity."""

import os
from hashlib import sha256

from .._bridge import operation
from .._profile import require_compatibility
from .terracotta_driver import TerracottaDriver

_CACHE = {}


def get_driver(url_or_path, provider=None):
    require_compatibility()
    path = str(url_or_path)
    try:
        info = operation(operation="provider_info", path=path, provider=provider)
    except Exception as error:
        raise ValueError(str(error)) from error
    from .mysql_meta_store import MySQLMetaStore
    from .postgresql_meta_store import PostgreSQLMetaStore
    from .sqlite_meta_store import SQLiteMetaStore
    from .sqlite_remote_meta_store import RemoteSQLiteMetaStore

    classes = {
        "sqlite": SQLiteMetaStore,
        "sqlite-remote": RemoteSQLiteMetaStore,
        "mysql": MySQLMetaStore,
        "postgresql": PostgreSQLMetaStore,
    }
    connection = (
        sha256(path.encode()).digest()
        if info["provider"] in ("mysql", "postgresql")
        else None
    )
    key = (info["identity"], info["provider"], os.getpid(), connection)
    if key not in _CACHE:
        _CACHE[key] = TerracottaDriver(classes[info["provider"]](path))
    return _CACHE[key]


__all__ = ["TerracottaDriver", "get_driver"]
