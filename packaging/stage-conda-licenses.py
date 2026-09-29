"""Retain licenses for every conda package contributing to the native bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for metadata_file in sorted((args.prefix / "conda-meta").glob("*.json")):
        package = json.loads(metadata_file.read_text())
        name, version = package["name"], package["version"]
        source = Path(package.get("extracted_package_dir", "")) / "info/licenses"
        destination = args.output / f"{name}-{version}"
        if source.is_dir():
            shutil.copytree(source, destination)
        manifest.append({"name": name, "version": version,
                         "license": package.get("license"), "url": package.get("url"),
                         "license_files": sorted(str(path.relative_to(destination))
                                                 for path in destination.rglob("*") if path.is_file())
                         if destination.is_dir() else []})
    if not manifest:
        raise RuntimeError("native prefix contains no conda package metadata")
    (args.output / "packages.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
