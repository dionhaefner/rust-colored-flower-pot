"""Catalog wrappers for native sessions."""

import contextlib
from collections import OrderedDict
from decimal import Decimal

from .. import get_settings
from .._bridge import Session, metadata, operation


class Catalog:
    provider = "sqlite"

    def __init__(self, path):
        self.path = str(path)
        self._session = None
        self._config = None
        self._depth = 0
        self._created = False
        if self.provider == "sqlite-remote":
            self._native()

    def _native(self):
        config = get_settings()._asdict()
        if self._session is None or (not self._depth and config != self._config):
            self._session = Session("store", self.path, self.provider, config)
            self._config = config
        return self._session

    @contextlib.contextmanager
    def connect(self, verify=True):
        native = self._native()
        if not self._depth:
            native.call(operation="version")
        self._depth += 1
        try:
            yield None
        finally:
            self._depth -= 1

    def _call(self, operation, **request):
        with self.connect():
            return self._native().call(
                driver=True, refresh=False, operation=operation, **request
            )

    @property
    def db_version(self):
        return self._call("version")

    @property
    def key_names(self):
        return tuple(self.get_keys())

    def create(self, keys, *, key_descriptions=None):
        operation(
            operation="driver_create",
            path=self.path,
            provider=self.provider,
            keys=list(keys),
            key_descriptions=key_descriptions or {},
        )
        self._session = None
        self._created = True

    def get_keys(self):
        return OrderedDict(
            (row["key"], row["description"]) for row in self._call("keys")
        )

    def get_datasets(
        self, where=None, page=0, limit=None, order_by=None, ascending=True
    ):
        rows = self._call(
            "datasets",
            where=where,
            page=page,
            limit=limit,
            order_by=order_by,
            ascending=ascending,
        )
        return {tuple(row["keys"]): row["path"] for row in rows}

    def _metadata(self, value):
        value = metadata(value)
        if value is not None and self.provider == "mysql" and not self._created:
            for field in ("bounds", "range", "mean", "stdev", "valid_percentage"):
                item = value[field]
                value[field] = (
                    tuple(Decimal(f"{v:.10f}") for v in item)
                    if isinstance(item, tuple)
                    else Decimal(f"{item:.10f}")
                )
        return value

    def get_metadata(self, keys):
        return self._metadata(self._call("metadata_stored", keys=keys))

    def insert(self, keys, path, *, metadata=None):
        return self._call(
            "insert", keys=keys, raster=str(path), metadata=metadata, skip_metadata=True
        )

    def delete(self, keys):
        return self._call("delete", keys=keys)
