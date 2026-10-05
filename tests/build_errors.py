"""Exercise build failure diagnostics through pip's wheel and editable hooks.

Requires pip and the Hatch build dependencies. The synthetic Cargo executable
fails before any compilation or downloads, so the original diagnostic and exit
status can be checked deterministically.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pip._vendor.pyproject_hooks import BuildBackendHookCaller

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux bundle build path")
class BuildErrorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rcfp-build-errors-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "metadata").mkdir()
        (self.root / "tracker").mkdir()
        packaging = self.root / "packaging"
        packaging.mkdir()
        for name in (
            "hatch_build.py",
            "build-bundle.sh",
            "build-python.sh",
            "cargo.sh",
        ):
            shutil.copy2(ROOT / "packaging" / name, packaging / name)
        (self.root / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["hatchling"]\n'
            'build-backend = "hatchling.build"\n'
            '[project]\nname = "rcfp"\nversion = "0.1.0"\n'
            '[tool.hatch.build.targets.wheel]\npackages = ["python/rcfp"]\n'
            "[tool.hatch.build.targets.wheel.hooks.custom]\n"
            'path = "packaging/hatch_build.py"\n'
        )
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        cargo = self.bin_dir / "cargo"
        cargo.write_text(
            '#!/bin/sh\nprintf "%s\\n" "error: controlled Cargo failure" >&2\nexit 101\n'
        )
        cargo.chmod(0o755)
        self.environment = os.environ.copy()
        for key in (
            "RCFP_BUNDLE_DIR",
            "RCFP_NATIVE_TARGET",
            "RCFP_GDAL_PREFIX",
            "RCFP_CARGO_OFFLINE",
        ):
            self.environment.pop(key, None)
        self.environment["PATH"] = str(self.bin_dir) + os.pathsep + os.environ["PATH"]
        # Hatch uses this pip marker to enable the wheel-building metadata fallback.
        self.environment["PIP_BUILD_TRACKER"] = str(self.root / "tracker")
        self.backend = BuildBackendHookCaller(
            str(self.root), "hatchling.build", runner=self.run_backend
        )

    def run_backend(self, command, cwd=None, extra_environ=None):
        environment = self.environment | (extra_environ or {})
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, command, output=result.stdout
            )

    def test_cargo_failure_in_wheel_and_editable(self):
        for method in (
            self.backend.prepare_metadata_for_build_wheel,
            self.backend.prepare_metadata_for_build_editable,
        ):
            with self.subTest(hook=method.__name__):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    method(str(self.root / "metadata"))
                output = caught.exception.output
                self.assertEqual(caught.exception.returncode, 1)
                self.assertIn("error: controlled Cargo failure", output)
                self.assertIn(
                    "RCFP native bundle build (Rust/GDAL) failed (exit status 101)",
                    output,
                )
                self.assertIn("Command:", output)
                self.assertIn("packaging/build-bundle.sh", output)
                self.assertIn("rust-toolchain.toml", output)
                self.assertIn("unset RCFP_CARGO_OFFLINE", output)
                self.assertNotIn("AttributeError", output)
                self.assertNotIn("Traceback", output)

    def test_missing_build_helper(self):
        (self.root / "packaging/build-bundle.sh").unlink()
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.backend.prepare_metadata_for_build_editable(
                str(self.root / "metadata")
            )
        output = caught.exception.output
        self.assertIn("RCFP native bundle build (Rust/GDAL) failed", output)
        self.assertIn("No such file or directory", output)
        self.assertIn("Command:", output)
        self.assertNotIn("AttributeError", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()
