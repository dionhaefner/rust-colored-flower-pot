"""Documented postgresql catalog type over the shared native store."""

from ._store import Catalog


class PostgreSQLMetaStore(Catalog):
    provider = "postgresql"
