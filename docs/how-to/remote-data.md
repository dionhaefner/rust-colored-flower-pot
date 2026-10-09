# Use remote rasters and catalogs

The metadata store and raster storage are separate choices. A local SQLite catalog
can point to remote rasters. An S3-hosted catalog is a downloaded read-only SQLite
snapshot. MySQL and PostgreSQL provide writable central metadata stores.

## Remote raster registration

```python
import rcfp

store = rcfp.DataStore("remote-catalog.sqlite")
store.create(["scene", "band"])
store.insert(["lagoon", "red"], "https://example.com/imagery/red.tif")
```

Replace the example URL with your accessible COG. Verify HTTP range requests and
GDAL-supported paths, masks, and overviews. Standard AWS credentials govern S3
catalog metadata access. Legacy `TC_RASTER_AWS_*` settings govern raster access.
Do not embed secrets in public examples or catalog URLs. Custom metadata S3
endpoints use `AWS_ENDPOINT_URL_S3`. The raster endpoint uses
`TC_RASTER_AWS_S3_ENDPOINT`.

## Publish a read-only S3 snapshot

Compute all metadata in a writable catalog first and upload an engine-consistent
SQLite backup to the chosen bucket. Register raster paths that the deployment
can reach. Set `TC_DRIVER_PATH=s3://your-bucket/catalog.sqlite` and test discovery
and imagery. Inserts, deletes, and schema upgrades are rejected on remote
snapshots. Missing metadata cannot be lazily written there.

`REMOTE_DB_CACHE_TTL` controls snapshot refresh. Native HTTP refreshes between
requests. The compatibility connection context pins a generation and refreshes
on reconnect. A failed refresh preserves the earlier file but reports an error.
It is not a silent stale-success policy. See [catalog reference](../reference/catalogs.md).

## Connect to SQL metadata

Select the provider explicitly when needed:

```python
import os
import rcfp

store = rcfp.DataStore(os.environ["CATALOG_URL"], provider="postgresql")
```

Use a verified TLS connection. PostgreSQL defaults to certificate/hostname
verification and supports `sslmode=verify-full` and an optional `sslrootcert`.
Plaintext `sslmode=disable` is permitted only with a literal loopback IP for local
engines. Remote MySQL also uses verified TLS. Test server certificates, URL
escaping, supported URL options, and credential rotation in your environment.
SQL provider tests are separate from SQLite checks. Obtain passwords through the
supported settings/secret mechanism rather than putting them in documentation.
