# CI and PyPI wheel handoff

Implemented [CI/GDAL design](../../CI.md) in `wheels.yml`,
`platforms.yml`, and `release.yml`. Linux uses Rasterio 1.4.4's GDAL 3.10.3
payload and `auditwheel`; macOS/Windows stage conda GDAL 3.10.3 and repair with
`delocate`/`delvewheel`. All five final wheel artifacts are installed on clean
CPython 3.12 runners before release reuse. `rcfp` declares CPython
`>=3.12,<3.13` and has no Rasterio runtime dependency. Linux builds still pin
Rasterio 1.4.4 as a GDAL payload source. The wheel carries its own GDAL,
PROJ data, core library, extension, and executable.

Local Linux work found and fixed a stale egg-info dependency leak, Rasterio
1.4.4's moved license path, auditwheel's missing nested-library search path,
and a source archive that omitted Cargo's test targets. A repaired local wheel
passed the expanded pinned Sentinel/SQLite/HTTP/WSGI/CLI smoke and another
real raster request after all test-only raster packages were uninstalled. The source
archive now excludes retained Terracotta source and validation evidence while
including required Rust test files and client license. See
[local evidence](../../validation/evidence/p08/ci-wheels/README.md).

No hosted CI run, tag, PyPI upload or AWS deployment was performed. Next:
run `wheels.yml` and `platforms.yml` on the actual hosted runners; repair any
macOS/Windows/arm64 failures from their reports. Configure the protected `pypi`
environment and PyPI Trusted Publisher only after reviewing the release scope
and binary licenses. The current compact wheel smoke does not rerun all P08
frozen-oracle, SQL, remote, stress, browser, real AWS or external basemap cases.
Several historical validators still import the old Python package names, so
those require migration before serving as new CI gates. P08/C27/C28/C32 remain
partial accordingly.
