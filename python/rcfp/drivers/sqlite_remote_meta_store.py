"""Documented sqlite-remote catalog type over the shared native store."""

from ._store import Catalog


class RemoteSQLiteMetaStore(Catalog):
    provider = "sqlite-remote"
