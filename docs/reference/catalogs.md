# Catalog formats and migrations

A catalog records ordered keys, key-value tuples, raster paths, and metadata.
Current native and compatibility modes use the Terracotta 0.10 encoding and
preserve its markers. Profile selection does not rewrite records or raster
files. The HTTP protocol version, catalog format, and RCFP package version are
separate identities.

Supported metadata providers are SQLite, MySQL, PostgreSQL, and read-only remote
SQLite snapshots. Raster files remain outside the catalog. Preserve path
interpretation and make the locations accessible in the target deployment.
Writable stores support metadata publication and lazy fill; a remote snapshot
must have its metadata completed before upload.

Historical upgrades cover supported 0.7/0.8/0.9 steps to 0.10. The 0.7 MySQL chain
is unsupported. SQLite and PostgreSQL migrations use transactions; MySQL DDL can
commit partially. S3 snapshots cannot be upgraded in place. `migrate --dry-run`
prints the planned chain without applying it. `--from` is an explicit override,
not automatic schema inference. Future/native-only markers are refused by the
compatibility write fence; no RCFP-only format is currently created.

Native HTTP snapshot refresh occurs between requests. The compatibility
connection context pins its current downloaded generation; reconnect after TTL
expiry can refresh it. Failed refresh reports an error while preserving the
prior file. Read the [remote guide](../how-to/remote-data.md) and
[backup/upgrade procedure](../how-to/backup-migrate.md) before write or upgrade trials.
