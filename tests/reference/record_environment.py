"""Record pinned-reference provenance and dependencies in ignored cache storage."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path

import PIL.features
import rasterio
import shapely
from fetch import SHA256, VERSION

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "6db38d5873cde5ebabe47338a1f61ddc788eada6"


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def manifest() -> dict:
    archive = ROOT / ".cache/reference/source.tar.gz"
    digest = sha256(archive)
    if digest != SHA256:
        raise ValueError("reference source archive does not match the pinned release")
    packages = {
        distribution.metadata["Name"]: distribution.version
        for distribution in importlib.metadata.distributions()
    }
    if packages["terracotta"] != VERSION:
        raise ValueError(
            "installed reference version does not match the pinned release"
        )
    native = {}
    site = Path(rasterio.__file__).parent.parent
    for folder in (
        "rasterio.libs",
        "shapely.libs",
        "rasterio/proj_data",
        "rasterio/gdal_data",
    ):
        for path in sorted((site / folder).rglob("*")):
            if path.is_file():
                native[str(path.relative_to(site))] = sha256(path)
    return {
        "reference_commit": COMMIT,
        "reference_version": VERSION,
        "source_archive_sha256": digest,
        "source_archive_name": archive.name,
        "source_modifications": "No application patch. PyPI sdist generates version metadata.",
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "packages": dict(sorted(packages.items())),
        "GDAL": rasterio.__gdal_version__,
        "PROJ": rasterio.__proj_version__,
        "GEOS": shapely.geos_version_string,
        "SQLite": sqlite3.sqlite_version,
        "codecs": {
            name: PIL.features.version(name) for name in PIL.features.get_supported()
        },
        "native_files": native,
    }


def main() -> None:
    output = ROOT / ".cache/reference"
    (output / "environment.json").write_text(
        json.dumps(manifest(), indent=2, allow_nan=False) + "\n"
    )
    frozen = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze", "--all"], text=True
    )
    (output / "requirements-resolved.txt").write_text(frozen)


if __name__ == "__main__":
    main()
