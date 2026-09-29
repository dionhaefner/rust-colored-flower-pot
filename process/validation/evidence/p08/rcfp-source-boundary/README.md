# RCFP source boundary and single-install wheel (2026-09-28)

Scope: local Linux x86_64 / CPython 3.12.11. This supersedes the package-layout claims in the earlier [identity evidence](../rcfp-identity/README.md). No hosted platform run or publication was performed.

The original `python/terracotta` tree has zero Git diff. The obsolete `python/terracotta_rust` adapter was removed after explicit user approval; the current `python/rcfp` adapter is separate. Browser assets were copied byte-for-byte into `src/assets/client` so Cargo and the source archive no longer depend on the original Terracotta tree. Cargo builds package and executable `rcfp` plus `rcfp_core`. The local source wheel command was:

```sh
TERRACOTTA_CARGO_OFFLINE=1 .venv-reference/bin/python -m pip wheel --no-deps --no-build-isolation . -w /tmp/rcfp-final-wheel
```

The local no-isolation wheel had SHA-256 `18e685dc2a77f3ac8cf087bda03c89024adda5c6de76e082916f95dc4196f96d`, 739 archive members and size 42,955,596 bytes. Its build environment had Rasterio 1.4.3/GDAL 3.9.3 despite the package metadata pinning Rasterio 1.4.4. This is an offline diagnostic wheel, not the normal install result. Its exact real-raster smoke passed native create/insert/render, discovery/metadata, RGB, compute, invalid query/CORS, WSGI and CLI checks.

The normal isolated build also passed:

```sh
TERRACOTTA_CARGO_OFFLINE=1 .venv-reference/bin/python -m pip wheel --no-deps . -w /tmp/rcfp-isolated-wheel
```

That wheel had SHA-256 `ded554026fd894992e23d610bf1d5a428bcaa766f95f67dda2316fe0dceff500`. It was installed with declared dependencies into a fresh CPython 3.12 venv; Rasterio 1.4.4 supplied GDAL 3.10.3. The same real-raster Python/WSGI/CLI smoke passed all seven checks. The wheel's only top-level package is `rcfp`; it includes `rcfp/_native.cpython-312-x86_64-linux-gnu.so`, `rcfp/runtime/lib/librcfp_core.so`, `rcfp/runtime/bin/rcfp`, GDAL/PROJ data and libraries, and the sole console entry `rcfp = rcfp.cli:main`. It has no `terracotta`, `terracotta_rust` or `rcfp_native` package or bytecode. Its metadata declares CPython `>=3.12,<3.13` and Rasterio `==1.4.4`.

A fresh environment then ran the README's exact `pip install .` command with build isolation and dependency resolution. `import rcfp` returned version 0.1.0, `rcfp --version` printed `rcfp 0.1.0`, and `rcfp serve` returned HTTP 200 with the expected `/keys` JSON from an existing real catalog. The earlier direct `--no-build-isolation --target` source install also passed import and CLI serving. `cargo check --locked --offline`, 20 Rust unit tests, Python compilation, shell syntax, formatting, and `git diff --check` passed.


A source archive was built with `python setup.py sdist`. `rcfp-0.1.0.tar.gz` has SHA-256 `9bf6d7dd3a74a208c36ecd5c12db3952567cd99096978a51b884603dd4b86406`, 302 members, and no `python/terracotta` or `python/terracotta_rust` files. It includes RCFP's own browser assets, build scripts and Cargo inputs. A wheel built from that tarball alone, with the workspace's cached Cargo toolchain explicitly on `PATH`, succeeded (SHA-256 `b007162be0714fe50869244ab21e0350b1f63614e961ac72883b01a6a34839e6`). A fresh environment installed it with Rasterio 1.4.4, `rcfp --version` returned 0.1.0, and a real catalog `/keys` request returned HTTP 200. The first tarball-build attempt failed only because this host keeps Cargo under the workspace's `.cache/cargo/bin`, which was absent from `PATH` inside pip's temporary extraction; repeating with that toolchain on `PATH` passed.

The wheel is a local `linux_x86_64` artifact and has not been repaired to a manylinux tag. The release verifier correctly rejected it as unrepaired; its content checks were separately inspected. Other platforms and portable-wheel library closure remain open. Online build dependency resolution succeeded locally, while hosted runner qualification remains open. The current RCFP Python API covers native requests, data operations and WSGI; the original Terracotta driver/Flask API was not copied into the wheel and is not yet implemented under `rcfp`.

Automatic approval review initially rejected deletion of the tracked `python/terracotta_rust` directory, treating it as comparison source. The user explicitly approved removing that obsolete adapter, after which the two files were removed directly with `git rm`. The original `python/terracotta` source remains unchanged.
