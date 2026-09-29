# Rust-colored flower pot (RCFP)

RCFP is a Rust implementation of the Terracotta API with a native server,
Python bindings, WSGI support, CLI, and browser client. It reads compatible
raster catalogs and serves discovery, metadata, and rendered imagery.

## Development checks

Install `pre-commit` with `python -m pip install pre-commit`, then run
`pre-commit install` once in your checkout. The
hooks format Rust with `cargo fmt`, Python with Ruff, C with clang-format, shell
scripts with shfmt, and maintained Markdown, YAML, JSON, HTML, CSS, and
JavaScript with Prettier.
Ruff, Clippy, and ShellCheck also lint staged source files. The Rust hooks use
the toolchain in `rust-toolchain.toml`; install its `rustfmt` and `clippy`
components before committing. Run `pre-commit run --all-files` to check the
repository on demand. A formatting hook that changes files requires restaging
those files before committing.

The installable package is named `rcfp`. Existing Python applications need to
change their import and command names when switching from Terracotta. For
installation and configuration, see [operator instructions](packaging/OPERATIONS.md).

## Install from source and try the CLI

The repository is a Python source package. On Linux x86_64, use CPython 3.12,
a C/C++ compiler, and `pip`. The PyPI `rustup` build dependency fetches the
pinned Rust 1.85.1 toolchain from `rust-toolchain.toml`; the build downloads
Python and Cargo dependencies on first use. On Linux, the build downloads
SHA-256-locked GDAL/PROJ archives directly from conda-forge and bundles their
native libraries and data. No conda executable or environment is needed. The
resulting wheel is specific to the building Python ABI and platform. The Linux
artifact targets glibc 2.34 or newer; a locally built wheel inherits the build host's native-library requirements.

From this repository's root, in a fresh environment:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install .
rcfp --help
python -c "import rcfp; print(rcfp.__version__)"
```

That single `pip install .` builds the Rust executable and shared core, compiles
the CPython extension, packages the native runtime and browser assets, and
creates the `rcfp` console command. Rasterio is not a build or runtime
dependency. The installed distribution and import package are both `rcfp`. The CLI launches the bundled Rust executable; Python
exposes native request, data-store, and WSGI APIs. RCFP keeps its browser
assets in `src/assets/client` and its Python adapter in `python/rcfp`.

For a first interactive check against a catalog you already have:

```sh
rcfp serve -d /absolute/path/catalog.sqlite
# In another terminal: curl http://127.0.0.1:5000/keys
```

To build a reusable wheel and install that exact file in another compatible
CPython 3.12 environment:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
python -m pip install dist/rcfp-*.whl
rcfp --version
```

The source build uses the Linux standalone bundle path. Set
`RCFP_GDAL_CACHE` to reuse verified downloaded native archives, or
`RCFP_GDAL_PREFIX` to reuse an existing GDAL/PROJ prefix. Set
`RCFP_CARGO_OFFLINE=1` when the Cargo registry is already cached.
For an offline local build with Python build requirements already installed, add
`--no-build-isolation` to `pip wheel` or `pip install`. macOS and Windows builds
use the platform staging path and require GDAL/PROJ in the build environment;
wheel repair embeds their native dependencies for installation. A wheel is installed with one `pip install` command. See
[operator instructions](packaging/OPERATIONS.md) for configuration and the
prebuilt artifact flow.
