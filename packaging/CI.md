# CI, GDAL packaging, and PyPI release

RCFP loads GDAL from a private runtime shipped with each wheel. Rasterio
1.4.4 is a pinned Python dependency and the Linux source of GDAL 3.10.3,
PROJ, codecs, and data. The Rust core still calls GDAL directly; installing
Rasterio alone does not expose its private libraries to a standalone Rust
binary. macOS and Windows wheel builders use conda GDAL 3.10.3, copy the
selected library and data into `rcfp/runtime`, then repair transitive shared
library dependencies. Wheel tests run after repair in clean pip environments.
This is the packaging choice recorded as D-018/RW-054.

## Implemented workflows

| Workflow | Trigger | Gate |
| --- | --- | --- |
| [Native platform qualification](../.github/workflows/platforms.yml) | PR, main, manual, release reuse | Five OS/architecture cells run fmt, Rust tests, Clippy, release build, and a pinned real-raster native smoke. Two Linux cells also build the AL2023 Lambda bundle and run the official runtime image smoke. |
| [Build and test wheels](../.github/workflows/wheels.yml) | PR, main, manual, release reuse | Build Linux x86_64/aarch64 with the AL2023 image and `auditwheel`; build macOS Intel/arm64 with conda and `delocate`; build Windows x86_64 with conda and `delvewheel`. For every final wheel, verify its archive, metadata, embedded runtime, and tag; install that exact artifact on a clean CPython 3.12 runner and run the real-raster Python/WSGI/CLI and route smoke. |
| [Publish to PyPI](../.github/workflows/release.yml) | `v*` tag | Reuse both qualification workflows, build sdist, verify all five unique wheels and their RECORD hashes, check tag/version and sdist contents, then upload the same artifacts with PyPI trusted publishing. Publish has no checkout or rebuild. |

The smoke uses a checksum-pinned Sentinel raster subset and a frozen
reference PNG. It creates the SQLite catalog through the installed package,
checks exact decoded singleband pixels, exercises discovery, metadata, RGB,
compute, invalid input, CORS, WSGI, and CLI. RGB/compute use three catalog
bands sourced from the same real crop. The native job also exercises the Lambda
event adapter and standalone binary. Reports are uploaded per platform.

The current wheel scope is CPython 3.12 only; package metadata says
`>=3.12,<3.13`. Rasterio 1.4.4 supplies a Linux arm64 wheel on PyPI, resolving
the clean-install gap of 1.4.3 (RW-051). A user invoking `pip install rcfp`
gets Rasterio from the normal Python dependency resolver. The Rust wheel
includes its own pinned GDAL because Rasterio's vendor libraries are private
implementation details and cannot be linked or loaded by RCFP portably.

## Release setup

Configure a PyPI Trusted Publisher for project `rcfp`, repository workflow
`release.yml`, and a protected GitHub environment named `pypi`. Protect `v*`
tags and main through repository rules. The release job requires the tag commit
to be on main, and the tag version to match wheel and sdist metadata. Only the
publish job receives `id-token: write`. No PyPI credential is stored in CI.
The workflow will publish automatically after its jobs and environment rule
pass. No tag, upload, or hosted-runner qualification was performed locally.

## Coverage still needed for full replacement qualification

The installed-wheel smoke is intentionally compact. The frozen-oracle P08
matrix, real SQL engines, remote socket emulator, large TIFF, concurrency,
resource, and browser tests have local historical evidence, but are not yet
wired into a fresh CI run. Several legacy validation scripts still import
`terracotta`/`terracotta_rust` after the accepted `rcfp` namespace change, so
those scripts must be adapted before they can serve as CI gates. P08's real AWS
and external basemap checks also remain outside CI until authorized services
exist. A green wheel/release workflow establishes portable package operation
on the five platforms; it does not by itself establish every P08 replacement
claim. See [execution status](../execution/STATUS.md) and
[coverage](../execution/COVERAGE.md) for the exact open claims.

Before the first public tag, review transitive binary license obligations and
run all five hosted wheel jobs. A local Linux wheel and syntax checks cannot
prove macOS/Windows DLL closure or PyPI trusted-publisher configuration.
