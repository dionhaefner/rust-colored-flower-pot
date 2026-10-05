"""Documented Terracotta driver methods backed by the shared native core."""

from .._bridge import metadata, operation, submit


class TerracottaDriver:
    def __init__(self, meta_store):
        self.meta_store = meta_store

    @property
    def key_names(self):
        return self.meta_store.key_names

    @property
    def db_version(self):
        return self.meta_store.db_version

    def create(self, keys, *, key_descriptions=None):
        return self.meta_store.create(keys, key_descriptions=key_descriptions)

    def connect(self, verify=True):
        return self.meta_store.connect(verify=verify)

    def get_keys(self):
        return self.meta_store.get_keys()

    def get_datasets(
        self, where=None, page=0, limit=None, order_by=None, ascending=True
    ):
        return self.meta_store.get_datasets(where, page, limit, order_by, ascending)

    def get_metadata(self, keys):
        return self.meta_store._metadata(self.meta_store._call("metadata", keys=keys))

    def insert(
        self, keys, path, *, override_path=None, metadata=None, skip_metadata=False
    ):
        return self.meta_store._call(
            "insert",
            keys=keys,
            raster=str(path),
            override_path=override_path,
            metadata=metadata,
            skip_metadata=skip_metadata,
        )

    def delete(self, keys):
        return self.meta_store.delete(keys)

    def compute_metadata(
        self, path, *, extra_metadata=None, use_chunks=None, max_shape=None
    ):
        import numpy as np

        result = metadata(
            operation(
                operation="compute_metadata",
                path=str(path),
                options={
                    "extra_metadata": extra_metadata,
                    "use_chunks": use_chunks,
                    "max_shape": max_shape,
                },
            )
        )
        result["percentiles"] = np.asarray(result["percentiles"], dtype="float64")
        return result

    def get_raster_tile(
        self,
        keys,
        *,
        tile_bounds=None,
        tile_size=(256, 256),
        preserve_values=False,
        asynchronous=False,
    ):
        store = self.meta_store
        native = store._native()
        request = {
            "keys": keys,
            "tile_bounds": tile_bounds,
            "tile_size": list(tile_size),
            "preserve_values": preserve_values,
            "refresh": not store._depth,
        }
        return (
            submit(native.raster, **request)
            if asynchronous
            else native.raster(**request)
        )
