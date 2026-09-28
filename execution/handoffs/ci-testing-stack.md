# CI testing stack design handoff

The requested CI and auto-publish design is in
[`packaging/CI.md`](../../packaging/CI.md). It maps fast checks, frozen-oracle
real-raster comparisons, disposable storage/deployment integration, five
installed-wheel platform cells, scheduled/release stress checks and a
protected-tag PyPI Trusted Publisher gate. It specifies that the exact tested
wheel files are the files uploaded to PyPI.

Inspection found that `.github/workflows/platforms.yml` currently builds
conda-dependent platform qualification wheels and runs only a narrow real-raster
smoke. `packaging/OPERATIONS.md` explicitly says macOS and Windows artifacts
require external conda GDAL. There are no retained non-Linux platform results.
The Linux x86_64 standalone CPython 3.12 wheel has local evidence, while package
metadata still advertises Python >=3.10. These must be resolved before an
automatic multi-platform publication gate can truthfully pass. P08's real AWS
and external basemap claims remain separate release readiness items.

No CI workflow or external PyPI configuration was changed, and no hosted run or
publish was performed. The design was checked against the repository's current
validation and packaging commands and the official PyPI/GitHub/cibuildwheel
documentation; no runtime test was appropriate for documentation-only changes.
Next implementation work is the three-step rollout in `packaging/CI.md`.
