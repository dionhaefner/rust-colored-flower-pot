"""Hatch wheel hook for the Rust core, CPython adapter, and private GDAL runtime."""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def _run(self, command: list[str], stage: str, hint: str, env: dict) -> None:
        try:
            subprocess.run(command, cwd=self.root, env=env, check=True)
        except (subprocess.CalledProcessError, OSError) as error:
            failure = (
                f"exit status {error.returncode}"
                if isinstance(error, subprocess.CalledProcessError)
                else str(error)
            )
            display_command = (
                subprocess.list2cmdline(command)
                if sys.platform == "win32"
                else shlex.join(command)
            )
            self._temporary.cleanup()
            self.app.abort(
                f"RCFP {stage} failed ({failure}).\n"
                f"Command: {display_command}\n"
                "See the build diagnostics above for the underlying error.\n"
                f"{hint}"
            )

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
                self._run(
                    [str(root / "packaging/build-bundle.sh"), str(bundle)],
                    "native bundle build (Rust/GDAL)",
                    "Check the Rust toolchain in rust-toolchain.toml and your C/C++ compiler. "
                    "Downloads require network access or cached dependencies; if Cargo reports "
                    "an uncached dependency, unset RCFP_CARGO_OFFLINE and retry.",
                    env,
                )
            staged = work / "package"
            self._run(
                [str(root / "packaging/build-python.sh"), str(bundle), str(staged)],
                "Python native adapter build",
                "Check your C compiler, Python development headers, and the native bundle. "
                "If RCFP_BUNDLE_DIR is set, ensure it contains a complete bundle.",
                env,
            )
        else:
            if not env.get("RCFP_NATIVE_TARGET"):
                self._run(
                    ["cargo", "build", "--locked", "--release"],
                    "Rust build",
                    "Check that Cargo and the Rust toolchain in rust-toolchain.toml are "
                    "installed, and that your C/C++ compiler is available. "
                    "Cargo downloads require network access or cached dependencies.",
                    env,
                )
            target = (
                Path(env.get("RCFP_NATIVE_TARGET", str(root / "target"))).resolve()
                / "release"
            )
            staged = work / "package"
            self._run(
                [
                    sys.executable,
                    str(root / "packaging/stage-platform-python.py"),
                    "--target",
                    str(target),
                    "--output",
                    str(staged),
                ],
                "native runtime staging",
                "Check the native build artifacts and GDAL/PROJ runtime. "
                "If RCFP_NATIVE_TARGET or RCFP_GDAL_PREFIX is set, ensure it is complete.",
                env,
            )

        runtime = staged / "rcfp" / "runtime"
        extensions = list((staged / "rcfp").glob("_native*"))
        if not runtime.is_dir() or len(extensions) != 1:
            raise RuntimeError("native package stage is incomplete")
        build_data["force_include"][str(runtime)] = "rcfp/runtime"
        build_data["force_include"][str(extensions[0])] = f"rcfp/{extensions[0].name}"
        platform_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        if sys.platform == "darwin":
            architecture = platform.machine()
            if architecture not in {"arm64", "x86_64"}:
                raise RuntimeError(f"unsupported macOS architecture: {architecture}")
            minimum = "11_0" if architecture == "arm64" else "10_15"
            platform_tag = f"macosx_{minimum}_{architecture}"
        build_data["tag"] = f"cp312-abi3-{platform_tag}"
        build_data["pure_python"] = False

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        self._temporary.cleanup()
