# Try RCFP with a Terracotta application

The `terracotta-0.10.1` compatibility profile targets Terracotta 0.10.1's public
Python, CLI, and HTTP behavior. Switch the package/import and command names;
keep your raster files, URLs, clients, and existing catalog. A valid catalog
that Terracotta 0.10.1 can open does not need migration or reingestion.

Install RCFP in a separate environment for the trial:

```sh
python -m pip install /path/to/rcfp-wheel.whl
export RCFP_COMPAT_PROFILE=terracotta-0.10.1
rcfp serve -d /absolute/path/catalog.sqlite
```

On Windows PowerShell:

```powershell
python -m pip install C:\downloads\rcfp-wheel.whl
$env:RCFP_COMPAT_PROFILE = "terracotta-0.10.1"
rcfp serve -d C:\data\catalog.sqlite
```

Replace `import terracotta as tc` with `import rcfp as tc`. An import alias covers
top-level calls; change submodule imports to `rcfp.*` as well:

```python
import rcfp as terracotta

driver = terracotta.get_driver("/absolute/path/catalog.sqlite")
with driver.connect():
    print(driver.get_keys())
    print(driver.get_datasets(limit=10))

terracotta.update_settings(DRIVER_PATH="/absolute/path/catalog.sqlite")
from rcfp.server.app import app
```

RCFP installs the `rcfp` command and import package. It does not replace an
installed `terracotta` command or import. Existing shell scripts need the same
command-name substitution as Python code needs for imports.

## Select the profile

The environment selector is frozen when Python first imports `rcfp`. Set it
before starting Python, your WSGI server, or your worker processes. Start a new
process to switch profiles. `rcfp.compatibility_profile` reports the selection;
`rcfp.__version__` remains the RCFP package version and
`rcfp.__protocol_version__` is the Terracotta protocol identity.

The CLI also accepts a global option or TOML setting:

```sh
rcfp --compat-profile terracotta-0.10.1 serve -d catalog.sqlite
rcfp -c settings.toml serve -d catalog.sqlite
```

```toml
COMPAT_PROFILE = "terracotta-0.10.1"
DEFAULT_TILE_SIZE = [256, 256]
```

CLI option > TOML `COMPAT_PROFILE` > `RCFP_COMPAT_PROFILE` > `native`. Global
options precede the subcommand. Existing `TC_*` settings retain their names and
explicit-setting precedence. Unknown profiles fail at startup. The profile
cannot be changed through `update_settings` or per-request settings.

## Compatibility scope

The Python contract follows Terracotta 0.10.1's API reference and documented
WSGI deployment entry point. Only the following entry points are supported:

| Surface          | Supported contract                                                                                                                                                                     |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Top-level Python | `get_driver`, `get_settings`, `update_settings`                                                                                                                                        |
| Settings result  | `rcfp.config.TerracottaSettings` named tuple and its documented fields                                                                                                                 |
| Driver           | `rcfp.drivers.TerracottaDriver`: `db_version`, `key_names`, `create`, `connect`, `get_keys`, `get_datasets`, `get_metadata`, `insert`, `delete`, `get_raster_tile`, `compute_metadata` |
| Provider types   | `SQLiteMetaStore`, `RemoteSQLiteMetaStore`, `MySQLMetaStore`, `PostgreSQLMetaStore` in their corresponding `rcfp.drivers.*_meta_store` modules                                         |
| Python results   | Ordered keys, dataset mappings, metadata tuples/lists, masked NumPy arrays, asynchronous `Future` results, and meaningful `rcfp.exceptions` types                                      |
| WSGI             | `rcfp.server.app:app`, backed by the native application through a small WSGI adapter                                                                                                   |
| CLI              | Rust implements `serve`, `ingest`, `optimize-rasters`, `connect`, and historical `migrate`, including supported Terracotta flags and configuration                                     |
| HTTP             | Discovery, metadata, singleband/RGB/compute tiles and previews, colormaps, CORS, API specification, and protocol version negotiation                                                   |
| Catalogs         | Terracotta 0.10 SQLite, MySQL and PostgreSQL catalogs; supported S3-hosted SQLite snapshots remain read-only                                                                           |
| Rasters          | Local and remote GeoTIFF/COG inputs supported by bundled GDAL, preserving paths, masks, nodata, values, CRS and overview behavior                                                      |
| Packaging        | CPython 3.12–3.14 on a matching wheel platform; see the [support matrix](reference/support.md)                                                                                         |

Raster I/O, reprojection, statistics, lazy metadata, catalog operations, rendering,
expressions, palettes, and CLI execution use shared Rust code. Python adapts
settings, exceptions, containers, NumPy arrays, Futures, and the WSGI protocol.
NumPy is the only Python runtime dependency; Flask and Click are unnecessary.

Importable Terracotta implementation modules are not a public API guarantee.
RCFP does not expose its internal `image`, `expressions`, `xyz`, `raster`,
`handlers`, `cmaps`, `logs`, `profile`, schema helpers or Click command objects.
Use the driver for array/metadata workflows and HTTP for rendering, expressions
and palettes. Custom backend hooks, raw SQLAlchemy connections, Flask app
factories/extensions, process-pool identity, and Python X-Ray decorators are also
outside this contract. The connection context pools native access and preserves
per-operation commits; its yielded value is not a SQL connection. `verify=False`
does not bypass catalog-format safety checks. CLI errors exit 2; `--profile`
reports native request timing rather than Python call-level profiling. Debugging
does not provide Flask's interactive debugger or automatic reload.

PNG compression bytes and explained minor numerical rounding may differ;
location, coverage, masks, categorical meaning and ordinary rendered appearance
must agree. The API documentation title is RCFP.

Both profiles enforce RCFP's [request and resource limits](reference/errors.md),
including request, tile-size, colormap and dataset-page bounds. Requests beyond
those bounds are outside the compatibility contract. SQL connections require
certificate and hostname verification by default; configure a trusted CA or
explicitly select plaintext only for a literal loopback address as described
in the [connection settings](how-to/remote-data.md#connect-to-sql-metadata).

Older catalog versions rejected by Terracotta 0.10.1 are also rejected here.
Their historical migration remains an explicit operation; merely selecting a
profile never upgrades a catalog. Unsupported/new format markers are rejected
before a write.

## Performance in compatibility mode

Compatible optimizations remain available. Worker-local GDAL handles, bounded
caches, and shared source geometry are used in both profiles. The qualified
Web Mercator fast path and neighbor prefetch can also be enabled:

```sh
export TC_RUST_MERCATOR_FAST=1
export TC_PREFETCH_TILE_RING=1
rcfp --compat-profile terracotta-0.10.1 serve -d catalog.sqlite
```

These paths preserve output and fall back for unsupported inputs. Prefetch uses
idle workers and bounded caches; choose budgets for your deployment. The UTM
sampler controlled by `TC_RUST_UTM_FAST` has confirmed pixel differences and is
disabled by compatibility mode, even when that variable is set. Native mode
can opt into it. Benchmark representative first views and navigation on your
own data before applying a speedup figure to your deployment.

## Validate the trial and return to Terracotta

Use a catalog copy for a staging trial. Compare key/dataset discovery, metadata,
representative tiles/previews, categorical layers, masks, and an ingestion
workflow. Keep the original raster locations accessible from both applications.

Reading metadata may fill missing metadata in a writable catalog, as it does in
Terracotta. A remote read-only catalog cannot perform that lazy write. Ordinary
RCFP writes preserve the Terracotta format, so the catalog remains usable by
Terracotta 0.10.1. To return, stop RCFP writers and launch Terracotta with the
same catalog and original settings. No reverse migration is needed while the
catalog remains in that format.

The named profile is included in the ordinary RCFP installation and is maintained
across future RCFP releases that ship it. Release CI checks its public contracts
against a pinned Terracotta 0.10.1 reference. There is no planned removal date;
any removal requires an announced support-policy change and major release.

See [adopt native mode](native-migration.md) when you are ready to change APIs or
enable optimizations with different outputs.
