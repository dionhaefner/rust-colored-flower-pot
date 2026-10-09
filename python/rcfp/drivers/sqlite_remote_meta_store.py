"""Remote SQLite catalog access through the native store."""

from ._store import Catalog


class RemoteSQLiteMetaStore(Catalog):
    provider = "sqlite-remote"
