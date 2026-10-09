"""MySQL catalog access through the native store."""

from ._store import Catalog


class MySQLMetaStore(Catalog):
    provider = "mysql"
