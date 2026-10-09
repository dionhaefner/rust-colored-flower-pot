"""PostgreSQL catalog access through the native store."""

from ._store import Catalog


class PostgreSQLMetaStore(Catalog):
    provider = "postgresql"
