# Install RCFP

Use CPython 3.12–3.14. Select a wheel for the operating system and architecture
where it will run. An `abi3` wheel supports all three Python versions on that
matching platform. See [artifact requirements](../reference/artifacts.md) and
[platform support](../reference/support.md).

## Install a wheel

Create an isolated environment, then install the wheel you obtained for your
release. Replace the path below with the exact file. This guide does not depend
on a package being available from PyPI.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install /path/to/rcfp-wheel.whl
rcfp --version
rcfp --help
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install C:\downloads\rcfp-wheel.whl
.venv\Scripts\rcfp.exe --version
.venv\Scripts\rcfp.exe --help
```

Wheels bundle the native executable, shared core, and GDAL/PROJ libraries and data.
You do not need a conda executable or Rasterio to run RCFP. The ordinary package
declares only NumPy for the compatibility API’s array return type. CLI, WSGI,
rendering and storage use the Rust core. Flask and Click are unnecessary.

## Install from source

From a source checkout or extracted source archive, with a C/C++ compiler and
Python headers available:

```sh
python -m pip install .
rcfp --help
```

The build uses Hatch and Hatch VCS for package versioning. Its pinned Rustup
helper obtains the Rust toolchain in `rust-toolchain.toml`. The first build also
downloads Python/Cargo dependencies and checksum-locked conda-forge native
runtime archives. It does not create a conda environment. The current toolchain
is Rust 1.88.0. A source-built wheel inherits the native requirements of its build
host. Release wheel repair determines the advertised platform tag.

For a reusable artifact:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
```

Install the exact resulting wheel file, as above. Use `RCFP_GDAL_CACHE` to reuse
verified native archives, `RCFP_GDAL_PREFIX` for an existing GDAL/PROJ prefix, and
`RCFP_CARGO_OFFLINE=1` when the Cargo registry is already cached. Fully offline
builds also require the Python build dependencies and native archives to be
available. `--no-build-isolation` alone does not supply them.

For contributor-only editable installation use `python -m pip install -e .`.
Reinstall after changing Rust/C code or the native runtime. Start
[your first map](../tutorials/first-map.md) when the version/help checks pass.

If a source or editable build fails, the final `RCFP ... failed` message names
the build step, command, and exit status. Read the Cargo/compiler diagnostics
immediately above it for the cause. Check the toolchain in `rust-toolchain.toml`,
your C/C++ compiler, and Python development headers. If Cargo cannot fetch an
uncached dependency while offline, unset `RCFP_CARGO_OFFLINE` and retry with
network access.
