"""Documented mysql catalog type over the shared native store."""

from ._store import Catalog


class MySQLMetaStore(Catalog):
    provider = "mysql"
