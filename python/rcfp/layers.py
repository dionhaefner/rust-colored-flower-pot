"""Database-backed native assets, observations and rendering."""

import json
import os
from urllib.parse import quote

from . import NativeApplication, RcfpError, _native, data_operation


def inspect_asset(path):
    """Inspect CRS, footprint and every band's numeric and category semantics."""
    return data_operation({"operation": "inspect_asset", "path": os.fspath(path)})


class LayerStore:
    """Persist complete native layer definitions and metadata in SQLite."""

    def __init__(self, database):
        self.path = os.fspath(database)

    def _call(self, operation, **fields):
        return data_operation(dict(operation=operation, path=self.path, **fields))

    def create(self):
        return self._call("layer_create")

    def register(self, name, raster, *, bands=None, stretch=None):
        """Register a one-file singleband or RGB layer with a stored default render."""
        return self._call(
            "layer_register",
            id=name,
            raster=os.fspath(raster),
            bands=bands,
            stretch=stretch,
        )

    def put(self, name, layer):
        """Inspect and atomically replace a complete layer definition."""
        return self._call("layer_put", id=name, layer=layer)

    def get(self, name):
        return self._call("layer_get", id=name)

    def list(self):
        return self._call("layer_ids")

    def delete(self, name):
        return self._call("layer_delete", id=name)

    def import_catalog(self, name, source, bindings):
        """Copy full-key dataset bindings into this database, preserving the source."""
        return self._call(
            "layer_import", id=name, source=os.fspath(source), bindings=bindings
        )


class LayerApplication(NativeApplication):
    """Own native layer workers and caches for repeated renders and array reads."""

    def render(self, name, spec=None):
        status, _content_type, data, _, _ = self.request(
            "POST",
            "/layers/" + quote(name, safe="") + "/render",
            {"content-type": "application/json"},
            json.dumps(spec or {}).encode(),
        )
        if status != 200:
            raise RcfpError(data.decode(errors="replace"))
        return data

    def samples(self, name, spec, *, provenance=False):
        """Return one f64 masked NumPy array per output channel, before display stretch."""
        import numpy as np

        result = json.loads(
            _native.session_operation(
                self._session,
                json.dumps(
                    {
                        "operation": "layer_samples",
                        "id": name,
                        "spec": spec,
                        "provenance": provenance,
                    }
                ),
            )
        )
        if "error" in result:
            raise RcfpError(result["error"])
        payload = result["ok"]
        samples = [
            np.ma.array(
                np.array(r["values"], dtype="float64").reshape(r["shape"]),
                mask=np.array(r["mask"], dtype=bool).reshape(r["shape"]),
            )
            for r in (payload["samples"] if provenance else payload)
        ]
        if provenance:
            return (
                samples,
                payload["contributors"],
                [
                    np.array(
                        [-1 if i is None else i for i in owners], dtype="int32"
                    ).reshape(samples[0].shape)
                    for owners in payload["provenance"]
                ],
            )
        return samples

    def plan(self, name, spec=None):
        """Select indexed mosaic candidates and check budgets without raster reads."""
        status, _, data, _, _ = self.request(
            "POST",
            "/layers/" + quote(name, safe="") + "/plan",
            {"content-type": "application/json"},
            json.dumps(spec or {}).encode(),
        )
        if status != 200:
            raise RcfpError(data.decode(errors="replace"))
        return json.loads(data)
