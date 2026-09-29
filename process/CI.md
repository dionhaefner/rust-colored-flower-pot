# CI, GDAL packaging, and PyPI release

RCFP loads GDAL from the private runtime in each wheel. Linux builds stage a
checksum-locked conda-forge GDAL/PROJ runtime and repair the wheel on the target
Ubuntu architecture. macOS and Windows builds stage the same locked runtime and
repair native dependencies with `delocate` or `delvewheel`. The Lambda bundle is
built separately in an AL2023 image and tested in the official runtime image.
The Linux wheel builder keeps auditwheel's CPU ISA check enabled; its glibc tag
must be installable on the oldest runner in the supported matrix.

## Workflows

| Workflow | Trigger | Gate |
| --- | --- | --- |
| [Native platforms](../.github/workflows/platforms.yml) | PR, main, manual, release reuse | Rust fmt, tests, Clippy, native real-raster smoke on Linux x86_64/aarch64, macOS x86_64/arm64, and Windows x86_64; AL2023 Lambda runtime smoke on both Linux architectures. |
| [Wheels](../.github/workflows/wheels.yml) | PR, main, manual, release reuse | Build and repair five `cp312-abi3` wheels; install each exact artifact on CPython 3.12, 3.13, and 3.14; run the standalone contract, range-read, and packaging smoke suites. |
| [Correctness](../.github/workflows/correctness.yml) | PR, main, manual, scheduled, release reuse | Candidate-versus-pinned-Terracotta HTTP/image comparison, live MySQL/PostgreSQL and remote-storage checks, upstream reference baseline, and Chromium client workflow. Scheduled, manual, and tag runs also verify a physical >4 GiB BigTIFF and concurrent resource behavior. |
| [Release](../.github/workflows/release.yml) | `v*` tag | Require all three qualification workflows, build and validate the sdist, verify exactly five tested wheels and version/RECORD metadata, then publish those artifacts to PyPI with trusted publishing. |

The standalone installed-wheel suite uses seven checksum-pinned real raster
windows. It checks database operations, HTTP and Python APIs, WSGI, CLI,
singleband/RGB/compute pixels, metadata, error routes, and package identity.
Its required case manifest rejects missing or skipped cases. SQL and browser
checks run through separate native jobs; their reports are retained as CI
artifacts. Test-only NumPy, Pillow, Rasterio, and Playwright are not package
runtime dependencies.

The pinned Terracotta 0.10.1 sdist is installed into a separate environment.
The differential job compares HTTP responses and decoded images. The upstream
baseline job expects 526 passes and the two recorded ANSI logging failures;
its six in-process remote-SQLite mock cases are excluded explicitly. This
reference baseline validates the oracle and does not replace standalone RCFP
assertions.

## Release setup

Configure a PyPI Trusted Publisher for project `rcfp`, repository workflow
`release.yml`, and a protected GitHub environment named `pypi`. Protect `v*`
tags and main through repository rules. The release job requires the tag commit
to be on main, and the tag version to match wheel and sdist metadata. Only the
publish job receives `id-token: write`. No PyPI credential is stored in CI.
The workflow publishes automatically after its jobs and environment rule pass.

Hatch VCS derives wheel and sdist versions from the checkout. Release wheel
builders fetch full Git history; the Lambda image is not used to build PyPI
wheels. Source builds need a C compiler, Rust toolchain, and the locked native
runtime bootstrap described in `packaging/`.
