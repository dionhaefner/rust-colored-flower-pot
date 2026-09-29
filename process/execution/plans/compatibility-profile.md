# Versioned Terracotta compatibility profile

## Decision and current state

This is the implementation design, not a claim that the current wheel provides
the profile. Today `rcfp` has a narrower Python API and installs only the `rcfp`
command. The existing HTTP, raster, and database work is a foundation, not proof
of Python or CLI drop-in compatibility. Do not advertise the trial workflow until
the release gates below pass on installed artifacts.

Ship a named, opt-in profile, `terracotta-0.10.1`, in the ordinary `rcfp`
distribution. It targets the checksum-pinned Terracotta 0.10.1 source release
used by this repository. The default profile is `native`. Every RCFP release
starting with the first release that ships this profile must continue to provide
it and run its gate. A release number, native API change, or new database format
must not silently change this profile. There is no planned removal date. Removal
would require a separately announced support-policy decision and a major
release; old RCFP versions cannot acquire this feature retroactively.

"Drop-in" has a precise, limited meaning here: after replacing
`import terracotta as tc` with `import rcfp as tc` (and `terracotta` CLI invocations
with `rcfp`), supported Terracotta 0.10.1 application workflows run against
unchanged raster files, raster URLs, and legacy catalogs. Imports of Terracotta
submodules need the equivalent `rcfp.*` prefix. The package does not install a
`terracotta` import or executable alias, since that would collide with an
installed upstream package. This one-time namespace edit and installation of
the RCFP wheel are the trial setup; no raster conversion or database migration
is part of it. Publish a coverage table of supported public APIs and deployment
patterns before using the drop-in label without a narrower qualifier.

## User contract

| Surface | `terracotta-0.10.1` profile | `native` profile |
| --- | --- | --- |
| Selection | `RCFP_COMPAT_PROFILE=terracotta-0.10.1` before Python import or process launch; for CLI, global `rcfp --compat-profile terracotta-0.10.1 ...` | Unset or `RCFP_COMPAT_PROFILE=native`; CLI may use `--compat-profile native` |
| Python | Legacy top-level names and `rcfp.*` modules, with Terracotta 0.10.1 call signatures, return types, exceptions, settings, driver cache/context behavior, Flask app factory, and WSGI behavior where supported upstream | Existing RCFP Python API; new APIs may evolve under RCFP's own versioning |
| CLI | Terracotta 0.10.1 commands, aliases, argument order, configuration precedence, exit behavior, and meaningful output; the executable name is `rcfp` | RCFP CLI and future commands/defaults |
| HTTP and images | Legacy routes, JSON, statuses, headers, version negotiation, defaults, and rendering meaning under the rewrite's documented visual tolerance | May add or change RCFP API behavior with its own versioning |
| Storage | Read/write existing supported Terracotta catalog formats and raster paths; preserve legacy encoding and version markers | May create new formats; legacy catalogs change format only through explicit migration |

Use one resolver for the process profile: CLI option > `COMPAT_PROFILE` configuration-file key >
`RCFP_COMPAT_PROFILE` environment variable > `native`. Python has no CLI option; its environment
selection is frozen on first `rcfp` import. Invalid/unknown values fail at
startup. Do not mutate the profile during a request or after a driver/app has
been created. The resolved profile is immutable in each driver, app, native
session, and worker. Explicit, documented request settings still override
defaults within the selected profile, but cannot alter its contract. The
profile name is a compatibility contract, not the RCFP implementation version
or the database version.

The trial guide should show, at minimum:

```sh
python -m pip install rcfp
export RCFP_COMPAT_PROFILE=terracotta-0.10.1
rcfp serve -d /absolute/path/existing-catalog.sqlite
```

```python
import rcfp as terracotta

driver = terracotta.get_driver("/absolute/path/existing-catalog.sqlite")
```

The Windows guide must show the equivalent environment setup. The Python
example is an intended contract, not currently runnable. Do not present it in
the product README until the profile and installed-wheel tests pass.

## Code boundaries

1. Add a typed `CompatibilityProfile`/policy in the shared core. Parse the
   setting once at adapter creation, pass the resolved value explicitly into
   storage, raster, HTTP, and native workers, and keep it in session ownership.
   Avoid reading a mutable process environment in inner functions or spreading
   unrelated profile checks across algorithms. Profile-specific defaults and
   known quirks should be named policy choices with focused tests.
2. Put the Python legacy facade in `python/rcfp` as thin adapters over the
   shared core. Start from the public inventory in
   [reference contracts](../../validation/reference/contracts.md) and
   [coverage inventory](../COVERAGE.md): `get_driver`, settings, drivers,
   connection contexts, metadata and raster methods, masked arrays, asynchronous results, exceptions,
   `rcfp.server.create_app`, WSGI, raster/image helpers, and CLI entry points.
   Alias import alone does not cover submodule imports. Keep any required Python
   dependencies in the base install if the flag alone promises the facade;
   an undocumented extra would break the trial. Preserve the existing native
   Python API in native mode and resolve any name collisions explicitly.
3. Use one CLI dispatch boundary for legacy parsing and native parsing. The
   global profile option is consumed before the command. Legacy mode invokes
   the same core operations with legacy validation/defaults; it should not
   shell out to a second Terracotta installation. Preserve `TC_*` configuration
   and add the RCFP profile selector without changing existing legacy settings.
4. Keep protocol identity `0.10.1`, legacy database identity, and RCFP package
   version separate. Do not use the wheel version to infer storage capability.
   Any optimization that changes externally relevant behavior must be gated by
   profile policy or proven within the retained compatibility bounds. A new
   native feature must not bypass the legacy adapter by accident.

## Catalog and raster safety

Compatibility mode reads and writes the existing SQLite, supported remote
SQLite, MySQL, and PostgreSQL layouts without automatic schema changes. Preserve
stored path interpretation, metadata encoding, key ordering, transaction and
lazy-metadata behavior. Test Terracotta-created catalogs with unchanged local
and supported remote rasters, then test RCFP writes read back through Terracotta.
Read-only access may still have the upstream lazy-metadata behavior; the guide
must distinguish pure inspection from operations that may fill metadata.

Native mode should continue reading legacy catalogs when feasible. A future
native-only schema is a new format with an explicit format marker and a separate
`rcfp migrate` operation: inspect and dry-run first, require an operator backup,
then migrate with an atomic swap where the backend permits it. Never migrate on
open or on profile selection. Compatibility mode must reject a native-only
catalog with a clear error before writing to it. Concurrent writers across
profiles are supported only while both use the same legacy encoding. Returning
to Terracotta after a native-format migration requires restoring the legacy
backup or an explicitly tested reverse migration; do not promise automatic
rollback.

## Moving from compatibility to native

Publish two guides when the profile ships:

* **Try RCFP with existing data:** exact supported Terracotta release, install
  and flag commands, mechanical import/CLI substitutions, provider/platform
  matrix, unmodified-catalog smoke, known accepted rendering differences, and
  how to return to Terracotta while the catalog remains in legacy format.
* **Adopt native mode:** a table of every observable native difference from the
  named profile (Python names/types, CLI options/defaults, HTTP behavior,
  rendering, deployment, and storage). Include equivalent before/after code,
  a validation checklist for representative real rasters, a staging plan,
  backup and rollback steps, and separate instructions for any optional format
  migration. Switching the flag alone must not mutate data.

Keep the differences table updated in the same change that introduces a native
break. If a behavior is not yet compatible, label its exact scope and impact;
the umbrella drop-in claim waits until all promised rows are qualified.

## CI and release gate

Build and install the exact wheel under test. Run the same required contract
manifest in both profiles, with profile-specific expectations; do not count a
native pass as a compatibility pass. In an isolated reference environment,
create catalogs and outputs using the pinned Terracotta 0.10.1 sdist. Compare:

* Python public imports/signatures, settings and driver behavior, return
  types/exceptions, Flask/WSGI integrations, and an application-style script
  using `import rcfp as terracotta`;
* every supported CLI workflow and option, configuration precedence, errors,
  exit codes, and a normal script invoking `rcfp`;
* HTTP responses and decoded imagery on the retained real-raster corpus,
  including the existing differential cases and edge cases;
* upstream-created legacy catalog read, RCFP insert/update/delete and upstream
  readback, untouched raster checksums, and database schema/marker checks for
  SQLite, MySQL, PostgreSQL, and supported remote storage;
* flag resolution, invalid value rejection, profile immutability, native-mode
  behavior, and a native-only-format refusal fixture when that format exists.

Run the Python/CLI/storage gates on pull requests, main, and release tags; run
the artifact smoke on each supported wheel platform and Python version. The
large raster/resource tier may remain scheduled, but a release cannot skip a
required compatibility case. Pin the oracle archive hash and dependency
environment. Record exclusions and accepted visual differences as named cases
with bounded assertions. A failure or missing required case blocks the
compatibility claim and release; the reference suite's own baseline is separate
from testing RCFP. Carry this gate forward on every later release that ships
the profile. Do not change the pinned target when Terracotta or RCFP versions
advance; add a new named profile if a later upstream version is supported.

## Implementation sequence and acceptance

1. Inventory the current gaps against the pinned public reference, especially
   the Python facade and CLI. Mark each supported or excluded surface explicitly.
2. Implement profile resolution and storage write fences; prove selection and
   unchanged legacy catalog access before adding facade methods.
3. Complete Python submodules, Flask/WSGI, and CLI workflows against the same
   reference cases. Add required dependencies and installed-wheel checks.
4. Add both migration guides and the exact support matrix to product docs;
   keep planning/evidence here. Wire the compatibility gate into release CI.
5. Qualify every advertised platform and provider. Only then mark the profile
   available and use the scoped drop-in language in the README and package
   description. Keep the gate mandatory in subsequent releases.

The work is complete when a new user can run the trial examples against an
existing Terracotta catalog without raster/database conversion, the documented
legacy Python and CLI workflows pass from an installed wheel, a same-format
catalog remains usable by upstream Terracotta, native mode has an explicit
differences/migration guide, and release CI enforces both modes.
