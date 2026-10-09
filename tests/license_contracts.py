"""Check native notice staging and platform Cargo collection without a build."""

from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class LicenseContracts(unittest.TestCase):
    def test_prefix_runtime_dependencies(self):
        spec = importlib.util.spec_from_file_location(
            "stage_gdal_runtime", ROOT / "packaging/stage-gdal-runtime.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            for platform, gdal, alias, dependency in (
                ("darwin", "libgdal.dylib", "libgdal.36.dylib", "libproj.dylib"),
                ("win32", "gdal.dll", "gdal310.dll", "proj.dll"),
            ):
                with self.subTest(platform=platform):
                    prefix = Path(temporary) / platform / "prefix"
                    base = prefix / "Library" if platform == "win32" else prefix
                    libraries = base / ("bin" if platform == "win32" else "lib")
                    libraries.mkdir(parents=True)
                    for name in (gdal, alias, dependency):
                        (libraries / name).write_bytes(name.encode())
                    for name in ("gdal", "proj"):
                        (base / "share" / name).mkdir(parents=True)
                    (base / "share/proj/proj.db").write_bytes(b"projection data")
                    runtime = Path(temporary) / platform / "runtime"
                    with patch.object(sys, "platform", platform):
                        module.stage(prefix, runtime)
                    self.assertEqual(
                        {path.name for path in (runtime / "lib").iterdir()},
                        {gdal, dependency},
                    )
                    self.assertEqual(
                        (runtime / "lib" / dependency).read_bytes(), dependency.encode()
                    )

    def test_shared_runtime_notices(self):
        spec = importlib.util.spec_from_file_location(
            "stage_licenses", ROOT / "packaging/stage-licenses.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "licenses"
            with patch.object(module.subprocess, "run") as collect:
                module.stage(output)
            for source, name in module.NOTICES.items():
                self.assertEqual(
                    (output / name).read_bytes(), (ROOT / source).read_bytes()
                )
            self.assertEqual(
                collect.call_args.args[0],
                [
                    sys.executable,
                    str(ROOT / "packaging/collect-licenses.py"),
                    str(output / "cargo"),
                ],
            )
            self.assertTrue(collect.call_args.kwargs["check"])

    def test_cargo_notices_on_each_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "crate"
            source.mkdir()
            (source / "Cargo.toml").write_text("")
            (source / "LICENSE-MIT").write_text("MIT notice")
            (source / "LICENSE-APACHE").write_text("Apache notice")
            package = {
                "name": "example",
                "version": "1.0.0",
                "manifest_path": str(source / "Cargo.toml"),
                "license": "MIT OR Apache-2.0",
                "source": "registry+https://example.invalid/index",
            }
            for platform in ("linux", "darwin", "win32"):
                with self.subTest(platform=platform):
                    output = Path(temporary) / platform
                    with (
                        patch.object(sys, "platform", platform),
                        patch.object(sys, "argv", ["collect-licenses.py", str(output)]),
                        patch(
                            "subprocess.check_output",
                            side_effect=[
                                "cargo 1.88.0\nhost: x86_64-unknown-linux-gnu\n",
                                json.dumps({"packages": [package]}).encode(),
                            ],
                        ) as cargo,
                    ):
                        runpy.run_path(str(ROOT / "packaging/collect-licenses.py"))
                    executable = (
                        "cargo"
                        if platform == "win32"
                        else str(ROOT / "packaging/cargo.sh")
                    )
                    self.assertEqual(cargo.call_args_list[0].args[0][0], executable)
                    inventory = json.loads((output / "manifest.json").read_text())
                    self.assertEqual(
                        inventory[0]["notices"], ["LICENSE-APACHE", "LICENSE-MIT"]
                    )
                    for name in inventory[0]["notices"]:
                        self.assertEqual(
                            (output / "example-1.0.0" / name).read_bytes(),
                            (source / name).read_bytes(),
                        )


if __name__ == "__main__":
    unittest.main()
