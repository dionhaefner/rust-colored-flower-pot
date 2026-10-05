"""Documented sqlite catalog type over the shared native store."""

from ._store import Catalog


class SQLiteMetaStore(Catalog):
    provider = "sqlite"
