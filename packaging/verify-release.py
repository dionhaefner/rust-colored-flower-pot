"""Validate a complete CPython 3.12–3.14 release artifact set."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import tarfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("tag")
    args = parser.parse_args()
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.tag):
        raise ValueError(f"release tag must be vMAJOR.MINOR.PATCH: {args.tag}")
    version = args.tag[1:]
    distributions = sorted(path for path in args.directory.iterdir() if path.is_file())
    wheels = [path for path in distributions if path.suffix == ".whl"]
    sdists = [path for path in distributions if path.name.endswith(".tar.gz")]
    if len(wheels) != 5 or len(sdists) != 1 or len(distributions) != 6:
        raise ValueError(f"expected exactly five wheels and one sdist: {distributions}")
    if sdists[0].name != f"rcfp-{version}.tar.gz":
        raise ValueError(f"sdist version differs from tag: {sdists[0].name}")
    with tarfile.open(sdists[0]) as archive:
        names = set(archive.getnames())
        prefix = f"rcfp-{version}/"
        required = {
            "Cargo.toml",
            "Cargo.lock",
            "pyproject.toml",
            "packaging/hatch_build.py",
            "packaging/bootstrap-gdal.py",
            "packaging/gdal-packages.lock.json",
            "packaging/stage-gdal-runtime.py",
            "tests/verify-runtime-without-rasterio.py",
            "packaging/licenses/CLIENT-LICENSE.txt",
            "packaging/cargo.sh",
            "tests/storage_sql.rs",
            "src/lib.rs",
            "python/rcfp/__init__.py",
            "python/rcfp_cli.py",
            "python/rcfp/_profile.py",
            "src/compatibility.rs",
            "src/python/driver.rs",
            "python/rcfp/drivers/_store.py",
            "python/rcfp/server/app.py",
            "docs/compatibility.md",
            "docs/native-migration.md",
        }
        if prefix + "setup.py" in names:
            raise ValueError("source distribution unexpectedly contains setup.py")
        missing = {name for name in required if prefix + name not in names}
        if missing:
            raise ValueError(f"source distribution is incomplete: {sorted(missing)}")
    spec = importlib.util.spec_from_file_location(
        "verify_wheel", Path(__file__).with_name("verify-wheel.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    platforms = set()
    for wheel in wheels:
        row = module.verify(wheel, version)
        tags = row["tags"]
        if not all(tag.startswith("cp312-abi3-") for tag in tags):
            raise ValueError(f"unexpected Python ABI: {wheel.name}: {tags}")
        name = wheel.name
        parts = wheel.stem.split("-", 4)
        if len(parts) != 5 or parts[:4] != ["rcfp", version, "cp312", "abi3"]:
            raise ValueError(f"unexpected wheel filename: {name}")
        filename_platforms = parts[4].split(".")
        if set(tags) != {f"cp312-abi3-{item}" for item in filename_platforms}:
            raise ValueError(f"wheel filename/WHEEL tag mismatch: {name}: {tags}")
        if all(
            re.fullmatch(r"manylinux_[0-9]+_[0-9]+_x86_64", item)
            for item in filename_platforms
        ):
            platform = "linux-x86_64"
        elif all(
            re.fullmatch(r"manylinux_[0-9]+_[0-9]+_aarch64", item)
            for item in filename_platforms
        ):
            platform = "linux-aarch64"
        elif all(
            re.fullmatch(r"macosx_[0-9]+_[0-9]+_x86_64", item)
            for item in filename_platforms
        ):
            platform = "macos-x86_64"
        elif all(
            re.fullmatch(r"macosx_[0-9]+_[0-9]+_arm64", item)
            for item in filename_platforms
        ):
            platform = "macos-arm64"
        elif filename_platforms == ["win_amd64"]:
            platform = "windows-amd64"
        else:
            raise ValueError(f"unexpected wheel platform: {name}")
        if platform in platforms:
            raise ValueError(f"duplicate wheel platform: {platform}")
        platforms.add(platform)
    expected = {
        "linux-x86_64",
        "linux-aarch64",
        "macos-x86_64",
        "macos-arm64",
        "windows-amd64",
    }
    if platforms != expected:
        raise ValueError(f"missing wheel platforms: {expected - platforms}")
    for path in distributions:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{digest}  {path.name}")


if __name__ == "__main__":
    main()
