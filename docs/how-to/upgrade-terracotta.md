# Upgrade from Terracotta

RCFP targets Terracotta 0.10.1 through the named `terracotta-0.10.1` profile.
Your supported raster files, URLs, and 0.10 catalogs can stay in place. Package,
import, command, and deployment entry-point names need deliberate changes.
Check [the support matrix](../reference/support.md) for your integration; private
Terracotta internals are outside the public compatibility contract.

## Choose your path

| Your application                  | Trial path                                         | Changes to review                                                                     |
| --------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Existing HTTP/browser clients     | Serve a catalog copy with RCFP on a second port    | Configuration, deployment, resource limits, requests, masks, and rendering            |
| Documented Python drivers or WSGI | [Compatibility-profile trial](../compatibility.md) | Import/submodule prefixes, CPython version, documented API coverage, WSGI entry point |
| CLI ingestion or shell scripts    | Compatibility profile, keeping legacy flags        | Executable name, configuration precedence, errors and exit status                     |
| New native application            | [Adopt native mode](../native-migration.md)        | DataStore/request API, exception types, WSGI entry point, cache/lifecycle behavior    |

Inventory your current Terracotta and catalog versions, backend, raster locations,
settings, scripts, imports, hosting model, and representative HTTP requests.
A catalog older than the format accepted by Terracotta 0.10.1 needs a separate
[schema-upgrade procedure](backup-migrate.md). Switching profiles never performs
that upgrade.

## Prepare a recoverable trial

Keep the old environment and its configuration. Install an RCFP wheel in a new
[environment](install.md). Stop writers and make an engine-consistent catalog
backup. For SQLite use the backup API, which includes committed WAL data:

```sh
python docs/examples/backup_catalog.py existing.sqlite trial.sqlite
```

Use a restored copy for writes and initial exploration. Lazy metadata requests
can write to a writable catalog; a filename that looks like a read-only task is
not a guarantee of pure inspection. Keep the original raster paths reachable
from both applications, and back up raster data independently if needed.

## Translate and start beside the old service

```sh
# Before, in the Terracotta environment:
terracotta serve -d existing.sqlite --port 5000
# Trial, in the RCFP environment:
rcfp --compat-profile terracotta-0.10.1 serve -d trial.sqlite --port 5001
```

```python
# Before:
import terracotta as tc
from terracotta.server.app import app

# After, with the profile selected before Python starts:
import rcfp as tc
from rcfp.server.app import app
```

An import alias covers top-level calls only. Change every public submodule prefix
and review [compatibility Python signatures](../reference/compatibility-python.md).
No `terracotta` import shim or executable alias is installed. See the
[trial guide](../compatibility.md) for PowerShell and TOML selectors, and the
[native differences](../native-migration.md) if you want to change APIs.

Keep applicable `TC_*` configuration names. Explicit CLI settings override TOML,
which overrides environment values. Profile selection is frozen before import
and application/driver creation. Review [configuration](../reference/configuration.md),
TLS/credentials, [resource budgets](resources.md), profiling, and shutdown behavior.

## Validate before cutover

Compare `/keys`, filtered `/datasets`, metadata values, and representative
singleband/RGB/compute requests. Include projected and geographic sources,
edge/transparent tiles, categorical labels, custom palettes, and all clients you
actually use. Compare decoded images and their coverage/masks rather than PNG
compression bytes. Verify supported writes by reopening the catalog with
Terracotta, then test errors, remote credentials, and realistic concurrency.

The downloadable [trial script](../examples/compatibility_trial.py) exercises
public driver discovery and a native request against a copied catalog:

```sh
RCFP_COMPAT_PROFILE=terracotta-0.10.1 python docs/examples/compatibility_trial.py trial.sqlite
```

Use your hosting system to switch its upstream from the old service to the trial
service after staging succeeds. Check discovery and an image through the real
public URL. Retain the previous service and configuration for rollback.

## Return to Terracotta

Stop RCFP writers before restarting the old service. While the catalog still
uses the supported 0.10 encoding, ordinary RCFP writes remain readable by
Terracotta 0.10.1; switching servers needs no reverse migration. Restore the
backup if a trial changed records you wanted to preserve, or if you explicitly
upgraded an older schema. Stop writers before a restore and validate the restored
catalog and imagery. Do not run writers with incompatible schema encodings.
