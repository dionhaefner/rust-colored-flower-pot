# Catalog formats and migrations

A catalog records ordered keys, key-value tuples, raster paths, and metadata.
Legacy catalog APIs in both profiles use the Terracotta 0.10 encoding and
preserve its markers. Native [keyed catalogs](layers.md) use the distinct
`rcfp-native-2` SQLite format for self-contained band bindings, expressions and
STAC snapshots. The advanced layer format retains its `rcfp-native-1` marker. Profile selection does not rewrite records or raster
files. The HTTP protocol version, catalog format, and RCFP package version are
separate identities.

`rcfp serve --source SOURCE` and the regular Python/WSGI application constructors
open catalog locations or local source configurations. `--database` remains a CLI
alias. Supported metadata providers are SQLite, MySQL, PostgreSQL, and read-only remote
SQLite snapshots. Native HTTP serving also adapts [STAC sources](../how-to/stac.md)
to the same dataset keys, metadata and rendering routes without a database import. Raster files remain outside the catalog. Preserve path
interpretation and make the locations accessible in the target deployment.
Writable stores support metadata publication and lazy fill; a remote snapshot
must have its metadata completed before upload.

Historical upgrades cover supported 0.7/0.8/0.9 steps to 0.10. The 0.7 MySQL chain
is unsupported. SQLite and PostgreSQL migrations use transactions; MySQL DDL can
commit partially. S3 snapshots cannot be upgraded in place. `migrate --dry-run`
prints the planned chain without applying it. `--from` is an explicit override,
not automatic schema inference. Future/native-only markers are refused by the
compatibility write fence. Import into a separate native keyed catalog is explicit
and preserves the original database for rollback; it is not a historical migration.

Native HTTP snapshot refresh occurs between requests. The compatibility
connection context pins its current downloaded generation; reconnect after TTL
expiry can refresh it. Failed refresh reports an error while preserving the
prior file. Read the [remote guide](../how-to/remote-data.md) and
[backup/upgrade procedure](../how-to/backup-migrate.md) before write or upgrade trials.
