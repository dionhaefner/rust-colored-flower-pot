"""Record actual installed benchmark code and native dependency fingerprints."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import sqlite3
from importlib.metadata import distributions, version
from pathlib import Path

import rasterio


def fingerprint(folder):
    hasher = hashlib.sha256()
    files = 0
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or not (path.suffix == ".py" or ".so" in path.name):
            continue
        hasher.update(str(path.relative_to(folder)).encode() + b"\0")
        with path.open("rb") as stream:
            hasher.update(hashlib.file_digest(stream, "sha256").digest())
        files += 1
    return {"sha256": hasher.hexdigest(), "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("terracotta", "titiler"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    modules = (
        ["rasterio", "terracotta", "gunicorn"]
        if args.engine == "terracotta"
        else ["rasterio", "titiler.core", "rio_tiler", "uvicorn"]
    )
    package_code = {}
    for module in modules:
        spec = importlib.util.find_spec(module)
        folder = Path(spec.origin).parent
        package_code[module] = fingerprint(folder)
        if module == "rasterio":
            package_code["rasterio.libs"] = fingerprint(folder.parent / "rasterio.libs")
    result = {
        "engine": args.engine,
        "python": platform.python_version(),
        "gdal": rasterio.__gdal_version__,
        "proj": rasterio.__proj_version__,
        "sqlite": sqlite3.sqlite_version,
        "rasterio": version("rasterio"),
        "packages": {d.metadata["Name"]: d.version for d in distributions()},
        "installed_code": package_code,
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
