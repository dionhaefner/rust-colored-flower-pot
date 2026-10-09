"""Stage a macOS/Windows wheel with a private GDAL and PROJ runtime.

The resulting wheel still needs delocate/delvewheel repair before distribution.
"""

import argparse
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--target", type=Path, default=ROOT / "target/release")
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
target, output = a.target.resolve(), a.output.resolve()
output.mkdir(parents=True, exist_ok=False)
shutil.copy2(ROOT / "README.md", output / "README.md")
for name in ("rcfp",):
    shutil.copytree(
        ROOT / "python" / name,
        output / name,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
lib = output / "rcfp/runtime/lib"
lib.mkdir(parents=True)
bin_dir = output / "rcfp/runtime/bin"
bin_dir.mkdir()
shutil.copy2(
    target / ("rcfp.exe" if sys.platform == "win32" else "rcfp"),
    bin_dir / ("rcfp.exe" if sys.platform == "win32" else "rcfp"),
)
if sys.platform == "win32":
    core = "rcfp_core.dll"
    link = target / "rcfp_core.dll.lib"
elif sys.platform == "darwin":
    core = "librcfp_core.dylib"
    link = lib / core
else:
    core = "librcfp_core.so"
    link = None
shutil.copy2(target / core, lib / core)
runtime = output / "rcfp/runtime"
if os.environ.get("RCFP_GDAL_PREFIX"):
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging/stage-gdal-runtime.py"),
            "--prefix",
            os.environ["RCFP_GDAL_PREFIX"],
            "--runtime",
            str(runtime),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging/stage-conda-licenses.py"),
            os.environ["RCFP_GDAL_PREFIX"],
            str(runtime / "licenses/conda"),
        ],
        check=True,
    )
else:
    with tempfile.TemporaryDirectory(prefix="rcfp-gdal-cache-") as temporary:
        cache = os.environ.get("RCFP_GDAL_CACHE", temporary)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "packaging/bootstrap-gdal.py"),
                "--output",
                str(runtime),
                "--cache",
                cache,
            ],
            check=True,
        )
if sys.platform == "darwin":
    subprocess.run(
        [sys.executable, str(ROOT / "packaging/prune-runtime-libs.py"), str(runtime)],
        check=True,
    )
    subprocess.run(
        ["install_name_tool", "-id", "@rpath/" + core, str(lib / core)], check=True
    )
subprocess.run(
    [
        sys.executable,
        str(ROOT / "packaging/stage-licenses.py"),
        str(runtime / "licenses"),
    ],
    check=True,
)
# Compile against the active CPython and the staged Rust import library.
include = sysconfig.get_paths()["include"]
suffix = ".abi3.pyd" if sys.platform == "win32" else ".abi3.so"
extension = output / "rcfp" / ("_native" + suffix)
source = str(ROOT / "python/native.c")
if sys.platform == "win32":
    # setuptools supplies MSVC environment discovery.
    from setuptools._distutils.ccompiler import new_compiler
    from setuptools._distutils.sysconfig import customize_compiler

    compiler = new_compiler()
    customize_compiler(compiler)
    objects = compiler.compile(
        [source],
        output_dir=str(output),
        include_dirs=[include],
        macros=[("Py_LIMITED_API", "0x030C0000")],
    )
    compiler.link_shared_object(
        objects,
        str(extension),
        extra_objects=[str(link)],
        library_dirs=[str(Path(sys.base_prefix) / "libs")],
        libraries=["python3"],
    )
else:
    command = [
        "cc",
        "-bundle",
        "-fPIC",
        "-DPy_LIMITED_API=0x030C0000",
        f"-I{include}",
        source,
        "-L" + str(lib),
        "-lrcfp_core",
        "-Wl,-rpath,@loader_path/runtime/lib",
        "-undefined",
        "dynamic_lookup",
        "-o",
        str(extension),
    ]
if sys.platform != "win32":
    subprocess.run(command, cwd=output, check=True)
