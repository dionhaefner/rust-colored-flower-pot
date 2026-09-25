"""Stage a wheel using an installed GDAL runtime (CI/macOS/Windows).

GDAL and its data/dependencies remain externally installed, e.g. with conda.
Linux standalone wheels instead use build-wheel.sh and the portable bundle.
"""
import argparse, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--target', type=Path, default=ROOT/'target/release')
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
target, output = a.target.resolve(), a.output.resolve()
output.mkdir(parents=True, exist_ok=False)
for name in ('terracotta', 'terracotta_rust'):
    shutil.copytree(ROOT/'python'/name, output/name, ignore=shutil.ignore_patterns('__pycache__'))
lib = output/'terracotta_rust/runtime/lib'
lib.mkdir(parents=True)
if sys.platform == 'win32':
    core = 'terracotta_core.dll'
    link = target/'terracotta_core.dll.lib'
    args = []
elif sys.platform == 'darwin':
    core = 'libterracotta_core.dylib'
    link = lib/core
    args = ['-Wl,-rpath,@loader_path/runtime/lib']
else:
    core = 'libterracotta_core.so'
    link = None
    args = ['-Wl,-rpath,$ORIGIN/runtime/lib']
shutil.copy2(target/core, lib/core)
if sys.platform == 'darwin':
    subprocess.run(['install_name_tool', '-id', '@rpath/'+core, str(lib/core)], check=True)
license_dir = output/'terracotta_rust/runtime/licenses'
license_dir.mkdir()
for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
    shutil.copy2(ROOT/name, license_dir/name)
# Compile with the interpreter's own setuptools platform compiler (MSVC on Windows).
build = output/'build-extension.py'
build.write_text('from setuptools import setup, Extension\nsetup(name="native-stage", ext_modules=[Extension("terracotta_rust._native", '
                 + repr([str(ROOT/'python/native.c')]) + ', extra_objects='+repr([str(link)] if link else [])
                 + ', libraries='+repr([] if link else ['terracotta_core'])
                 + ', library_dirs='+repr([str(lib)])
                 + ', extra_link_args='+repr(args)+')])\n')
subprocess.run([sys.executable, str(build), 'build_ext', '--inplace'], cwd=output, check=True)
build.unlink()
shutil.copy2(ROOT/'packaging/python-setup.py', output/'setup.py')
