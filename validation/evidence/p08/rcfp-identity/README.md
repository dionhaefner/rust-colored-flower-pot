> Historical package-layout evidence. The user subsequently required the original Terracotta source to remain untouched; the current RCFP wheel and limits are recorded in [source-boundary evidence](../rcfp-source-boundary/README.md).

# RCFP Python distribution identity (2026-09-28)

Scope: Linux x86_64 / CPython 3.12.11, local cached build. This supersedes the earlier `terracotta-rust`/`terracotta` source-wheel experiment in [source-pip-rcfp](../source-pip-rcfp/README.md). The frozen Python oracle, database schema, native Rust library name and separate native bundle executable were not renamed.

## Reproduction

```sh
TERRACOTTA_CARGO_OFFLINE=1 .venv-reference/bin/python -m pip wheel --no-cache-dir --no-deps --no-build-isolation . --wheel-dir /tmp/rcfp-identity-final-clean-wheels
.venv-reference/bin/python -m venv /tmp/rcfp-identity-env
/tmp/rcfp-identity-env/bin/python -m pip install --no-index --no-deps /tmp/rcfp-identity-final-clean-wheels/*.whl
PYTHONPATH=/tmp/rcfp-identity-env/lib/python3.12/site-packages:.venv-reference/lib/python3.12/site-packages /tmp/rcfp-identity-env/bin/rcfp --version
TERRACOTTA_CARGO_OFFLINE=1 .venv-reference/bin/python -m pip install --no-cache-dir --no-deps --no-build-isolation --target /tmp/rcfp-identity-final-source .
.venv-reference/bin/python setup.py sdist --dist-dir /tmp/rcfp-identity-release-sdist
TERRACOTTA_NATIVE_TARGET=target TERRACOTTA_REFERENCE_PYTHON="$PWD/.venv-reference/bin/python" packaging/build-bundle.sh /tmp/rcfp-identity-final-bundle
PYTHON=.venv-reference/bin/python packaging/build-wheel.sh /tmp/rcfp-identity-final-bundle /tmp/rcfp-identity-final-staged-wheels
```

The final wheel `rcfp-0.1.0-cp312-cp312-linux_x86_64.whl` has 988 members, 32,553,010 bytes, SHA-256 `c094aaec2ea62594f59e83c91a17d5dc8425b6424198d7afd4e1798772bc3d57`. Its only console entry is `rcfp = rcfp.scripts.cli:entrypoint`. It contains `rcfp`, `rcfp_native`, the compiled CPython extension, Rust shared core, GDAL/PROJ runtime and browser/API assets. It contains no `terracotta` or `terracotta_rust` Python package and no `runtime/bin/terracotta` executable. A fresh wheel environment found neither legacy import. `pip` identified the installed distribution as `rcfp` 0.1.0; `rcfp --version` printed 0.1.0.

The final source install succeeded and exposed `import rcfp`, `RcfpSettings`, `rcfp.__version__ == '0.1.0'` and `rcfp.__protocol_version__ == '0.10.1'`; its target had `bin/rcfp` and neither old Python package directory. The staged-bundle wheel path also produced an `rcfp` wheel with the new driver module and no old package. The final `rcfp-0.1.0.tar.gz` source archive had 1,508 members, including Cargo inputs, both RCFP source packages and build scripts, with no old Python package paths.

Installed-wheel runtime checks used the fresh wheel packages with runtime dependencies borrowed through `PYTHONPATH` from `.venv-reference`. They passed: public `RcfpDriver`, `RcfpSettings`, `RcfpError`, a 256×256 real uint16 tile, Flask `/keys`, real PNG preview, WSGI `/keys`, and `/swagger.json` with title `RCFP` and protocol version `0.10.1`. Installed `rcfp.cmaps` loaded the viridis resource, and the browser template returned the `RCFP Preview` title. No `terracotta` module was loaded by these calls. Bash syntax, Python compileall and `git diff --check` passed. The first renamed wheel was rejected because stale `build/lib` files carried both old package directories; `BuildWithRust` now clears only this project's package directories in that generated tree before each source wheel build. A later audit found local `__pycache__` files copied by the Linux staging script, including one created by its own import smoke. The stage now removes existing bytecode and disables bytecode writes for that smoke. The final wheel has neither old package paths nor bytecode.

Build isolation, dependency resolution, macOS/Windows and the hosted CI matrix were not exercised in this local offline check. Existing Python applications must change their import from `terracotta` to `rcfp` and invoke `rcfp`; this is an intentional user-directed namespace difference, not legacy drop-in import compatibility. Previously sealed P08 artifacts retain their older package names and were not modified.
