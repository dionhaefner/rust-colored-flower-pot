# CI, GDAL packaging, and PyPI release

RCFP loads GDAL from a private runtime shipped with each wheel. Linux source
builds create an isolated conda-forge GDAL 3.10.3/PROJ prefix with a pinned,
SHA-256-verified micromamba binary. The build copies native libraries, data,
and their license files into `rcfp/runtime`. macOS and Windows wheel builders
use conda GDAL 3.10.3, copy the selected library and data into `rcfp/runtime`,
then repair transitive shared library dependencies. Wheel tests run after repair in clean pip environments.

## Implemented workflows

| Workflow                                                            | Trigger                         | Gate                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Native platform qualification](../.github/workflows/platforms.yml) | PR, main, manual, release reuse | Five OS/architecture cells run fmt, Rust tests, Clippy, release build, and a pinned real-raster native smoke. Two Linux cells also build the AL2023 Lambda bundle and run the official runtime image smoke.                                                                                                                                                                 |
| [Build and test wheels](../.github/workflows/wheels.yml)            | PR, main, manual, release reuse | Build Linux x86_64/aarch64 with the AL2023 image and `auditwheel`; build macOS Intel/arm64 with conda and `delocate`; build Windows x86_64 with conda and `delvewheel`. For every final wheel, verify its archive, metadata, embedded runtime, and tag; install that exact artifact on a clean CPython 3.12 runner and run the real-raster Python/WSGI/CLI and route smoke. |
| [Publish to PyPI](../.github/workflows/release.yml)                 | `v*` tag                        | Reuse both qualification workflows, build sdist, verify all five unique wheels and their RECORD hashes, check tag/version and sdist contents, then upload the same artifacts with PyPI trusted publishing. Publish has no checkout or rebuild.                                                                                                                              |

The smoke uses a checksum-pinned Sentinel raster subset and an expected
PNG described in [the release smoke fixtures](../tests/README.md). It creates the SQLite catalog through the installed package,
checks exact decoded singleband pixels, exercises discovery, metadata, RGB,
compute, invalid input, CORS, WSGI, CLI, and a live HTTP server started
through the installed command. CI then removes Rasterio, NumPy, and Pillow and
repeats a native raster request to prove the wheel runs without them.
RGB and compute use three catalog bands sourced from the same real crop. The native job also exercises the Lambda
event adapter and standalone binary. Reports are uploaded per platform.

The current wheel scope is CPython 3.12 only; package metadata says
`>=3.12,<3.13`. A user invoking `pip install rcfp` installs no Rasterio package. Linux
source builds also need no Rasterio; they fetch native GDAL/PROJ packages into
a temporary prefix, or use `RCFP_GDAL_PREFIX` when provided.

## Release setup

Configure a PyPI Trusted Publisher for project `rcfp`, repository workflow
`release.yml`, and a protected GitHub environment named `pypi`. Protect `v*`
tags and main through repository rules. The release job requires the tag commit
to be on main, and the tag version to match wheel and sdist metadata. Only the
publish job receives `id-token: write`. No PyPI credential is stored in CI.
The workflow will publish automatically after its jobs and environment rule
pass.
