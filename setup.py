"""Build the Python package and its Rust core from this source checkout."""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).parent / "packaging" / "python-setup.py"))
