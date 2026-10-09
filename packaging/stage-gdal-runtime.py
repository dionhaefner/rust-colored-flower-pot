"""Copy the selected conda GDAL runtime into a platform wheel stage.

GDAL uses libloading, so stage it before delocate (macOS) or delvewheel (Windows)
repair. These tools cannot discover it from the extension's link table.
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
    # Prefer the unversioned link and retain one GDAL payload to avoid loading
    # two different versions.
    preferred = "gdal.dll" if sys.platform == "win32" else "libgdal.dylib"
    selected = next(
        (path for path in candidates if path.name == preferred), candidates[0]
    )
    lib_out = runtime / "lib"
    lib_out.mkdir(parents=True, exist_ok=True)
    target = lib_out / selected.name
    shutil.copy2(selected.resolve(), target)
    suffix = "*.dll" if sys.platform == "win32" else "*.dylib"
    for library in sorted(libraries.glob(suffix)):
        if "gdal" not in library.name.lower():
            shutil.copy2(library.resolve(), lib_out / library.name)

    for name in ("gdal", "proj"):
        source = share / name
        if not source.is_dir():
            raise FileNotFoundError(f"missing {name} data: {source}")
        shutil.copytree(source, runtime / "share" / name)
    certificates = (
        (prefix / "Library" if sys.platform == "win32" else prefix)
        / "ssl"
        / "cacert.pem"
    )
    if certificates.is_file():
        shutil.copy2(certificates, runtime / "share" / "cacert.pem")
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
