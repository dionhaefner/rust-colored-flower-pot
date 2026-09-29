"""Console entry point for the Rust RCFP executable bundled with this wheel."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from . import __version__


def main() -> int:
    executable = Path(__file__).resolve().parent / "runtime" / "bin" / (
        "rcfp.exe" if os.name == "nt" else "rcfp"
    )
    if not executable.is_file():
        raise SystemExit(f"RCFP native executable is missing: {executable}")
    args = sys.argv[1:]
    env = os.environ.copy()
    if os.name == "nt":
        env["PATH"] = str(executable.parent.parent / "lib") + os.pathsep + env.get("PATH", "")
    elif sys.platform == "darwin":
        env["DYLD_LIBRARY_PATH"] = str(executable.parent.parent / "lib") + os.pathsep + env.get("DYLD_LIBRARY_PATH", "")
    if args == ["--version"]:
        print(f"rcfp {__version__}")
        return 0
    if os.name == "nt":
        return subprocess.call([str(executable), *args], env=env)
    os.execve(str(executable), [str(executable), *args], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
