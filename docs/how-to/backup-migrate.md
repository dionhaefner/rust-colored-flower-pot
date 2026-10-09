# Back up and upgrade a catalog

A catalog backup preserves records and metadata, not the raster files those
records reference. Stop writers before an upgrade/restore and preserve the old
software/configuration as well as the catalog.

## Back up SQLite safely

Use SQLite's backup API so committed WAL pages are included. Avoid copying only
the main database file while WAL writes are active.

```sh
python docs/examples/backup_catalog.py catalog.sqlite catalog-backup.sqlite
```

```{literalinclude} ../examples/backup_catalog.py
:language: python
```

Choose a new destination path. Check the backup by starting a server against a
copy and comparing discovery, metadata, and representative images. For MySQL or
PostgreSQL use an engine-consistent dump or snapshot and rehearse restoration
into a disposable database before upgrading production.

## Inspect and apply a historical upgrade

Both profiles preserve the current Terracotta 0.10 format. A supported current
catalog needs no upgrade for RCFP. For an older supported catalog, operate on a
restored copy first:

```sh
rcfp migrate older-copy.sqlite --to 0.10.1 --dry-run
rcfp migrate older-copy.sqlite --to 0.10.1 --yes
```

The dry run prints the planned SQL without applying it. `--from VERSION` is an
explicit source-version override when the stored version cannot be read. Use it
only after inspecting the actual schema. The 0.7 MySQL chain is unsupported, and
read-only S3 snapshots cannot be upgraded in place. Consult
[catalog format reference](../reference/catalogs.md) for boundaries.

SQLite and PostgreSQL upgrades are transactional. MySQL DDL may partially commit,
so restore the original backup before retrying a failed upgrade. There is no
promised reverse migration. To return to an older schema, restore the matching
backup and old application after stopping writers. Profile selection alone
never changes the format marker.
