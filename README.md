# Rust-colored flower pot (RCFP)

<img src="docs/static/rcfp-logo-256.png" alt="RCFP flower-pot logo" width="128" height="128">
<img src="docs/static/rcfp-logo-crab-256.png" alt="RCFP crab flower-pot logo" width="128" height="128">

RCFP serves raster imagery from existing GeoTIFFs and catalogs through a shared
Rust core. It includes a native server, Python/WSGI interfaces, CLI, and browser
client. Matching wheels bundle the executable and GDAL/PROJ runtime; no conda
environment or Rasterio runtime is required.

- [Get started](docs/tutorials/first-map.md): create a map from supplied real imagery.
- [Upgrade from Terracotta](docs/how-to/upgrade-terracotta.md): keep supported data,
  translate application entry points, and rehearse rollback.
- [Why RCFP?](docs/explanation/why-rcfp.md): deployment choices, explicit capacity
  controls, performance opportunities, and tradeoffs.
- [Documentation](docs/README.md): Diátaxis tutorials, how-to guides, reference,
  explanation, and local HTML build instructions.

## Performance

In a four-core Linux navigation benchmark, native RCFP completed first viewports
**1.5–4.2× faster** and pans **1.9–9.5× faster** than the tested Terracotta and
TiTiler/rio-tiler configurations. Three rotating-order trials used large real
Sentinel-2, elevation and land-cover rasters, SQLite, one/three users, equal
aggregate cache budgets and PNG output. All **45,360 requests succeeded**.
RGB uses three separate band COGs.

| Storage / layer | First view vs Terracotta | First view vs TiTiler | Pan vs Terracotta | Pan vs TiTiler |
| --- | ---: | ---: | ---: | ---: |
| Local / singleband | 4.2× | 3.6× | 9.5× | 8.7× |
| Local / RGB | 3.6× | 3.9× | 5.8× | 4.6× |
| Remote ranges / singleband | 2.0× | 1.5× | 2.4× | 1.9× |
| Remote ranges / RGB | 2.4× | 1.6× | 4.0× | 2.6× |

These are medians across the tested raster/concurrency cells for the measured
development build. Remote ranges use a self-hosted origin with 15 ms source
latency; browser paint, cold disk and actual S3 are outside this comparison.
Sampling policies differ and fidelity was checked separately. See the
[versions, timings, fidelity results and reproduction method](docs/explanation/performance.md#terracotta-and-titiler-navigation-comparison).

## Install and try it

From this checkout, use CPython 3.12–3.14 with a C/C++ compiler and Python headers:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install .
rcfp --version
python docs/examples/create_catalog.py map.sqlite
rcfp serve -d map.sqlite
```

Open `http://127.0.0.1:5000/keys` or follow the
[first-map tutorial](docs/tutorials/first-map.md) to open imagery and the client.
The source build obtains the pinned Rust toolchain and native archives on first
use. A matching prebuilt wheel avoids compilation. See [installation](docs/how-to/install.md)
for wheel/source, PowerShell, offline inputs, and platform requirements.

## Existing Terracotta applications

The `terracotta-0.10.1` profile targets supported public Terracotta 0.10.1 Python,
WSGI, CLI, and HTTP workflows. Select it before importing RCFP or launching:

```sh
rcfp --compat-profile terracotta-0.10.1 serve -d /absolute/path/catalog-copy.sqlite
```

Change `terracotta` import/submodule and command prefixes to `rcfp`. The package
installs no `terracotta` alias. Supported current catalogs and raster files need
no conversion; older schema upgrades are explicit. Read the
[trial guide](docs/compatibility.md), [native-mode differences](docs/native-migration.md),
and [support matrix](docs/reference/support.md) before switching a deployment.
Performance claims depend on workload, configuration, and verified artifacts.

## Development

See [maintainer context](design/README.md) for design decisions, compatibility
constraints, and outstanding qualification. [Contributor instructions](docs/contributing.md) cover formatting, docs builds,
example verification, CI, and versioned hosting. [Test instructions](tests/README.md)
cover product suites. Package builds use Hatch VCS versioning; preserve its build
configuration and generated version file. [How-to guides](docs/how-to/index.md)
cover installation, configuration, deployment, and recovery tasks.
