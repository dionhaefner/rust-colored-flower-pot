"""Hatch wheel hook for the Rust core, CPython adapter, and private GDAL runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        self._temporary = tempfile.TemporaryDirectory(prefix="rcfp-wheel-")
        work = Path(self._temporary.name)
        env = os.environ.copy()
        env["PYTHON"] = sys.executable
        env["RCFP_BUILD_PYTHON"] = sys.executable

        if sys.platform.startswith("linux"):
            bundle = (
                Path(env["RCFP_BUNDLE_DIR"]).resolve()
                if env.get("RCFP_BUNDLE_DIR")
                else work / "bundle"
            )
            if not env.get("RCFP_BUNDLE_DIR"):
                subprocess.run(
                    [str(root / "packaging/build-bundle.sh"), str(bundle)],
                    cwd=root,
                    env=env,
                    check=True,
                )
            staged = work / "package"
            subprocess.run(
                [str(root / "packaging/build-python.sh"), str(bundle), str(staged)],
                cwd=root,
                env=env,
                check=True,
            )
        else:
            if not env.get("RCFP_NATIVE_TARGET"):
                subprocess.run(
                    ["cargo", "build", "--locked", "--release"],
                    cwd=root,
                    env=env,
                    check=True,
                )
            target = (
                Path(env.get("RCFP_NATIVE_TARGET", str(root / "target"))).resolve()
                / "release"
            )
            staged = work / "package"
            subprocess.run(
                [
                    sys.executable,
                    str(root / "packaging/stage-platform-python.py"),
                    "--target",
                    str(target),
                    "--output",
                    str(staged),
                ],
                cwd=root,
                env=env,
                check=True,
            )

        runtime = staged / "rcfp" / "runtime"
        extensions = list((staged / "rcfp").glob("_native*"))
        if not runtime.is_dir() or len(extensions) != 1:
            raise RuntimeError("native package stage is incomplete")
        build_data["force_include"][str(runtime)] = "rcfp/runtime"
        build_data["force_include"][str(extensions[0])] = f"rcfp/{extensions[0].name}"
        platform_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        build_data["tag"] = f"cp312-abi3-{platform_tag}"
        build_data["pure_python"] = False

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        self._temporary.cleanup()
