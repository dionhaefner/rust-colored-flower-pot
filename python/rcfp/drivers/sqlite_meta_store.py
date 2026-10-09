"""SQLite catalog access through the native store."""

from ._store import Catalog


class SQLiteMetaStore(Catalog):
    provider = "sqlite"
