"""Copy the selected conda GDAL runtime into a platform wheel stage.

The wheel repair step must subsequently close and relocate shared-library
dependencies (delocate on macOS, delvewheel on Windows). GDAL is loaded with
libloading, so it must be staged explicitly: a repair tool cannot discover it
from the Rust extension's link table.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def stage(prefix: Path, runtime: Path) -> dict[str, Path]:
    if sys.platform == "win32":
        libraries = prefix / "Library" / "bin"
        share = prefix / "Library" / "share"
        candidates = sorted(libraries.glob("gdal*.dll"))
    elif sys.platform == "darwin":
        libraries = prefix / "lib"
        share = prefix / "share"
        candidates = sorted(libraries.glob("libgdal*.dylib"))
    else:
        raise RuntimeError("use build-bundle.sh for Linux wheels")

    if not candidates:
        raise FileNotFoundError(f"no GDAL shared library in {libraries}")
    # Choose the unversioned link name when available; retain only one GDAL
    # payload so that a wheel cannot accidentally load two different versions.
    preferred = "gdal.dll" if sys.platform == "win32" else "libgdal.dylib"
    selected = next((path for path in candidates if path.name == preferred), candidates[0])
    lib_out = runtime / "lib"
    lib_out.mkdir(parents=True, exist_ok=True)
    target = lib_out / selected.name
    shutil.copy2(selected.resolve(), target)

    for name in ("gdal", "proj"):
        source = share / name
        if not source.is_dir():
            raise FileNotFoundError(f"missing {name} data: {source}")
        shutil.copytree(source, runtime / "share" / name)
    return {"gdal": target, "source": selected, "data": runtime / "share"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path(sys.prefix))
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    staged = stage(args.prefix.resolve(), args.runtime.resolve())
    print("GDAL runtime staged:", staged)


if __name__ == "__main__":
    main()
