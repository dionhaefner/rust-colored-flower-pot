"""Build either the source tree or an already staged native package."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from setuptools import setup, find_packages, Distribution
from setuptools.command.build_py import build_py
class NativeDistribution(Distribution):
    def has_ext_modules(self):
        return True


class BuildWithRust(build_py):
    """Compile and stage the native runtime before assembling the wheel."""

    def run(self):
        # A prior build may have used the old Python package names. Clear only
        # this project's generated package directories before rebuilding.
        for name in ("terracotta", "terracotta_rust", "rcfp", "rcfp_native"):
            shutil.rmtree(Path(self.build_lib) / name, ignore_errors=True)
        super().run()
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="terracotta-wheel-") as temporary:
            temporary = Path(temporary)
            env = os.environ.copy()
            env["TERRACOTTA_REFERENCE_PYTHON"] = sys.executable
            env["PYTHON"] = sys.executable
            if sys.platform.startswith("linux"):
                bundle = temporary / "bundle"
                subprocess.run([str(root / "packaging/build-bundle.sh"), str(bundle)],
                               cwd=root, env=env, check=True)
                staged = temporary / "package"
                subprocess.run([str(root / "packaging/build-python.sh"), str(bundle), str(staged)],
                               cwd=root, env=env, check=True)
            else:
                subprocess.run(["cargo", "build", "--locked", "--release"],
                               cwd=root, env=env, check=True)
                staged = temporary / "package"
                subprocess.run([sys.executable, str(root / "packaging/stage-platform-python.py"),
                                "--output", str(staged)], cwd=root, env=env, check=True)
            for name in ("rcfp",):
                shutil.copytree(staged / name, Path(self.build_lib) / name, dirs_exist_ok=True)


root = Path.cwd()
source_tree = (root / "Cargo.toml").is_file()
package_root = root / "python" if source_tree else root
packages = find_packages(where="python" if source_tree else ".", include=["rcfp", "rcfp.*"])
data = {
    package: [str(path.relative_to(package_root / package.replace(".", "/")))
              for path in (package_root / package.replace(".", "/")).rglob("*")
              if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".py"]
    for package in packages
}
setup(name='rcfp', version='0.1.0', description='RCFP raster tile server with a shared Rust core',
      packages=packages, package_dir={'': 'python'} if source_tree else {},
      package_data=data, distclass=NativeDistribution,
      cmdclass={'build_py': BuildWithRust} if source_tree else {},
      python_requires='>=3.12,<3.13', install_requires=['rasterio==1.4.4'],
      entry_points={'console_scripts':['rcfp=rcfp.cli:main']})
