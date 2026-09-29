# Migration and return to Python

Terracotta 0.10.1 uses `import terracotta`; this replacement installs
`import rcfp`. They can be kept in separate environments to rehearse rollback.
Preserve the Terracotta executable/environment, configuration, custom palettes
and raster paths until the migration is complete.
The implementation version is 0.1.0; compatible protocol/database identity stays
0.10.1. Ordinary Rust writes do not migrate that format.

## Rehearse on copies

1. Record source versions, key names/descriptions, dataset counts and ordered
   keys, extra metadata, raster paths, settings and representative requests.
2. Quiesce catalog writers. Take an engine-consistent backup: SQLite's backup
   API (including committed WAL data), or a consistent SQL engine dump/snapshot.
   Do not copy just a live SQLite main file while its WAL still contains writes.
3. Restore into a disposable catalog and verify it through the Terracotta 0.10.1
   environment. Keep a checksum of the backup; never test by altering production.
4. If the catalog predates 0.10, run the replacement's `migrate COPY -y`, with
   `--from` only when required by the actual historical format. Verify ordered
   keys, metadata and rendered images in both implementations before switching.
5. Run preparation/ingestion/overwrite/delete against the disposable catalog,
   then reopen it in Python and compare metadata, masks and representative images.

## Switch back after ordinary compatible writes

Stop accepting writes and let admitted raster work drain. Stop the replacement,
retain the current database, and point the preserved Python installation at the
same compatible database and raster paths. Confirm `/keys`, `/datasets`, selected
metadata and a tile/preview before returning traffic. No downgrade operation is
needed for unchanged 0.10-format Rust writes. Keep both the pre-change backup and
the latest catalog so a rollback does not silently discard later valid ingestion.

## Switch back after a historical migration

A historical Python version may require its historical schema. Restore the
pre-migration backup into a separate destination and run the matching old Python
version against it. Retain the migrated database for later reconciliation of
new writes. Do not edit the version marker to simulate a schema downgrade.

SQLite and PostgreSQL supported migration chains are transactional. MySQL DDL
can commit partially: after failure, restore the original copy before retrying.
The Terracotta 0.7 MySQL migration chain contains unsupported SQL and is not
a supported upgrade route. Use a database version supported by the matching
Terracotta installation when restoring a historical schema.

S3 metadata snapshots are read-only. A returned Python service must refresh or
restart its snapshot cache after an externally managed object replacement. Verify cloud credential and role refresh in the target environment before
returning traffic.
