# Superseded source pip installation experiment (2026-09-28)

The current package identity is `rcfp`; see [current evidence](../rcfp-identity/README.md). This file records the earlier `terracotta-rust`/`terracotta` wheel before the user clarified the import name.

Scope: local Linux x86_64, CPython 3.12.11, setuptools 80.9.0, wheel 0.45.1, Rasterio 1.4.3, pinned Rust 1.85.1. No new platform qualification or index publication is claimed. The existing native/Python runtime code was not changed by this task.

Commands run from the repository root:

```sh
TERRACOTTA_CARGO_OFFLINE=1 .venv-reference/bin/python -m pip wheel --no-cache-dir --no-deps --no-build-isolation . --wheel-dir /tmp/terracotta-rcfp-wheels
.venv-reference/bin/python -m venv /tmp/rcfp-wheel-env
/tmp/rcfp-wheel-env/bin/python -m pip install --no-index --no-deps /tmp/terracotta-rcfp-wheels/*.whl
PYTHONPATH=.venv-reference/lib/python3.12/site-packages /tmp/rcfp-wheel-env/bin/rcfp --help
TERRACOTTA_CARGO_OFFLINE=1 .venv-reference/bin/python -m pip install --no-cache-dir --no-deps --no-build-isolation --target /tmp/rcfp-source-install .
PYTHONPATH=/tmp/rcfp-source-install /tmp/rcfp-source-install/bin/rcfp --help
PYTHONPATH=/tmp/rcfp-source-install .venv-reference/bin/python -c 'import terracotta,terracotta_rust; print(terracotta_rust.identity("process/validation/artifacts/map10-baseline/catalog.sqlite"))'
.venv-reference/bin/python setup.py sdist --dist-dir /tmp/rcfp-source-dist
PYTHON=.venv-reference/bin/python packaging/build-wheel.sh /tmp/rcfp-staged-bundle /tmp/rcfp-staged-wheels-relative
PYTHONPATH=/tmp/rcfp-wheel-env/lib/python3.12/site-packages:.venv-reference/lib/python3.12/site-packages /tmp/rcfp-wheel-env/bin/rcfp serve -d process/validation/artifacts/map10-baseline/catalog.sqlite --port 5099
# Queried /keys and /__terracotta/identity over localhost, then stopped the server.
git diff --check
```

Results: both `pip wheel .` and direct `pip install .` built and installed successfully. The final wheel is 43,952,531 bytes and has a CPython 3.12/Linux x86_64 platform tag. Its `entry_points.txt` contains only `rcfp = terracotta.scripts.cli:entrypoint`; `bin/terracotta` was absent from both fresh install locations. The wheel contains the CPython `_native` extension, `libterracotta_core.so`, the bundled native executable and GDAL runtime, the API template, and browser assets. `rcfp --help` showed `Usage: rcfp` and all five CLI subcommands. A native identity read from the local real-raster catalog returned implementation `0.1.0`. The existing staged-bundle wheel flow also built and registered only `rcfp` using a relative virtual-environment `PYTHON` path. The final installed `rcfp serve -d process/validation/artifacts/map10-baseline/catalog.sqlite --port 5099` responded with HTTP 200 on `/keys` and `/__terracotta/identity`; the server was stopped cleanly. The source archive contains `pyproject.toml`, setup/build scripts, Cargo manifest/lock, Rust source, Python source and executable `packaging/cargo.sh`; `git diff --check` passed.

The local build used cached Cargo crates and preinstalled Python build requirements (`--no-build-isolation`), and skipped dependency resolution (`--no-deps`). The fresh wheel environment borrowed Python runtime dependencies from `.venv-reference` through `PYTHONPATH`; its own `terracotta` and native extension files were confirmed to load from the fresh install. An isolated online build and dependency solve were not exercised. The tested source path still invokes the locked Cargo release build when `TERRACOTTA_NATIVE_TARGET` is unset. The first staged-wheel check used a missing/incomplete local P08 bundle and then a relative Python path that failed after the script changed directories. The bundle was regenerated and the script now makes relative Python paths absolute without resolving the virtual-environment symlink; the final staged check passed. Existing sealed P08 artifacts predate the `rcfp` rename and were not altered.
