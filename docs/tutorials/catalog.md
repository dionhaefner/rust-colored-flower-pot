# Build and inspect a raster catalog

You will build a second catalog and inspect it through Python. Use the same
installed environment and sample data as [your first map](first-map.md). Choose a
new filename: the example refuses to replace an existing catalog.

```sh
python docs/examples/create_catalog.py catalog.sqlite
```

Here is the script you just ran. The checksum check makes the sample reproducible;
`DataStore.create` fixes the key order; `insert` registers one raster per dataset.

```{literalinclude} ../examples/create_catalog.py
:language: python
```

Open Python from the repository root:

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
WorldCover layer. Key values select records; they need not match filenames.

Now create a backup with the supplied [SQLite backup script](../how-to/backup-migrate.md):

```sh
python docs/examples/backup_catalog.py catalog.sqlite catalog-backup.sqlite
```

The backup remains a catalog, referring to the same raster locations. It is not
a backup of the raster files themselves. Keep this copy for the migration trial.
See [ingestion](../how-to/ingest.md) to register your own data and
[the Python reference](../reference/python.md) for native operation semantics.
