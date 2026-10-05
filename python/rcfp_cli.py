"""Launch the bundled Rust CLI without importing the Python API."""

import os
import subprocess
import sys
from importlib.metadata import distribution


def main():
    package = distribution("rcfp")
    executable = package.locate_file(
        "rcfp/runtime/bin/rcfp.exe" if os.name == "nt" else "rcfp/runtime/bin/rcfp"
    )
    if not executable.is_file():
        raise SystemExit(f"RCFP native executable is missing: {executable}")
    arguments = sys.argv[1:]
    if arguments == ["--version"]:
        print(f"rcfp {package.version}")
        return 0
    env = os.environ.copy()
    if os.name == "nt" or sys.platform == "darwin":
        variable = "PATH" if os.name == "nt" else "DYLD_LIBRARY_PATH"
        env[variable] = (
            str(executable.parent.parent / "lib") + os.pathsep + env.get(variable, "")
        )
    if os.name == "nt":
        return subprocess.call([str(executable), *arguments], env=env)
    os.execve(str(executable), [str(executable), *arguments], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
