# Build and inspect a raster catalog

You will build a second catalog and inspect it through Python. Use the same
installed environment and sample data as [your first map](first-map.md). Choose a
new filename because the example refuses to replace an existing catalog.

```sh
python sample/create_catalog.py catalog.sqlite
```

The script verifies sample checksums, fixes the key order with `DataStore.create`,
and registers one raster per dataset with `insert`.

```{literalinclude} ../examples/create_catalog.py
:language: python
```

Open Python from the directory containing your catalog:

```python
import json
import rcfp

store = rcfp.DataStore("catalog.sqlite")
metadata = store.get_metadata(["lagoon", "red"])
print(metadata["bounds"])
status, content_type, body, cacheable = rcfp.request(
    "catalog.sqlite", "GET", "/datasets"
)
assert status == 200
print(json.loads(body))
```

The bounds are geographic coordinates even though the Sentinel sample is stored
in UTM zone 32N. The discovery response lists the four lagoon bands and the
WorldCover layer. Key values select records. They need not match filenames.

If you also have a source checkout or archive, create a backup with its supplied
[SQLite backup script](../how-to/backup-migrate.md). Run from that source root and
replace the paths below with your catalog and backup locations:

```sh
python docs/examples/backup_catalog.py /path/to/catalog.sqlite /path/to/catalog-backup.sqlite
```

The backup remains a catalog, referring to the same raster locations. It is not
a backup of the raster files themselves. Keep this copy for the migration trial.
See [ingestion](../how-to/ingest.md) to register your own data and
[the Python reference](../reference/python.md) for native operation semantics.
