"""Download the locked conda-forge GDAL runtime without a conda environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import zstandard

LOCK = Path(__file__).with_name("gdal-packages.lock.json")
CHANNEL = "https://conda.anaconda.org/conda-forge"


def download(package: dict, cache: Path) -> Path:
    archive = cache / package["subdir"] / package["filename"]
    archive.parent.mkdir(parents=True, exist_ok=True)
    expected = package["sha256"]
    if (
        archive.is_file()
        and hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    ):
        return archive
    url = f"{CHANNEL}/{package['subdir']}/{package['filename']}"
    partial = archive.with_name(archive.name + ".part")
    digest = hashlib.sha256()
    with (
        urllib.request.urlopen(url, timeout=90) as response,
        partial.open("wb") as output,
    ):
        while block := response.read(1024 * 1024):
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != expected or partial.stat().st_size != package["size"]:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"archive checksum or size mismatch: {url}")
    partial.replace(archive)
    return archive


def platform_key() -> str:
    arch = platform.machine().lower()
    if sys.platform == "darwin" and arch in ("x86_64", "arm64"):
        return {"x86_64": "osx-64", "arm64": "osx-arm64"}[arch]
    if sys.platform == "win32" and arch in ("amd64", "x86_64"):
        return "win-64"
    if sys.platform.startswith("linux") and arch in ("x86_64", "aarch64"):
        return arch
    raise RuntimeError(f"unsupported GDAL bootstrap platform: {sys.platform}/{arch}")


def payload_path(
    name: str, output: Path, license_dir: Path, target_platform: str
) -> Path | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"unsafe archive path: {name}")
    if len(path.parts) == 2 and path.parts[0] == "lib":
        if target_platform.startswith("osx-") and ".dylib" in path.name:
            return output / name
        if target_platform in ("x86_64", "aarch64") and ".so" in path.name:
            return output / name
    if (
        target_platform == "win-64"
        and len(path.parts) == 3
        and path.parts[:2] == ("Library", "bin")
        and path.name.lower().endswith(".dll")
    ):
        return output / "lib" / path.name
    share = ("Library", "share") if target_platform == "win-64" else ("share",)
    if path.parts in (("ssl", "cacert.pem"), ("Library", "ssl", "cacert.pem")):
        return output / "share" / "cacert.pem"
    if (
        len(path.parts) >= len(share) + 2
        and path.parts[: len(share)] == share
        and path.parts[len(share)] in ("gdal", "proj")
    ):
        return output / "share" / Path(*path.parts[len(share) :])
    if len(path.parts) >= 3 and path.parts[:2] == ("info", "licenses"):
        return license_dir.joinpath(*path.parts[2:])
    return None


def extract_component(
    archive: zipfile.ZipFile,
    component: str,
    output: Path,
    license_dir: Path,
    target_platform: str,
) -> list[str]:
    member_name = next(
        (
            name
            for name in archive.namelist()
            if name.startswith(component + "-") and name.endswith(".tar.zst")
        ),
        None,
    )
    if member_name is None:
        raise RuntimeError(f"missing {component} component in {archive.filename}")
    licenses = []
    with (
        archive.open(member_name) as compressed,
        zstandard.ZstdDecompressor().stream_reader(compressed) as stream,
        tarfile.open(fileobj=stream, mode="r|") as files,
    ):
        for member in files:
            target = payload_path(member.name, output, license_dir, target_platform)
            if target is None or member.isdir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                destination = (target.parent / member.linkname).resolve()
                if not destination.is_relative_to(output.resolve()):
                    raise RuntimeError(f"unsafe library symlink: {member.name}")
                if target.is_symlink():
                    if os.readlink(target) != member.linkname:
                        raise RuntimeError(f"conflicting library symlink: {target}")
                elif target.exists():
                    raise RuntimeError(f"library symlink conflicts with file: {target}")
                else:
                    target.symlink_to(member.linkname)
            elif member.isfile():
                source = files.extractfile(member)
                if source is None:
                    raise RuntimeError(f"missing archive payload: {member.name}")
                with source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                target.chmod(member.mode & 0o777)
            else:
                raise RuntimeError(f"unsupported archive member: {member.name}")
            if target.is_relative_to(license_dir):
                licenses.append(str(target.relative_to(license_dir)))
    return licenses


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--platform", dest="target_platform")
    args = parser.parse_args()
    target_platform = args.target_platform or platform_key()
    packages = json.loads(LOCK.read_text()).get(target_platform)
    if packages is None:
        raise RuntimeError(f"unsupported GDAL bootstrap platform: {target_platform}")
    output, cache = args.output.resolve(), args.cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        archives = list(pool.map(lambda package: download(package, cache), packages))
    inventory = []
    for package, archive_path in zip(packages, archives):
        license_dir = (
            output / "licenses/conda" / f"{package['name']}-{package['version']}"
        )
        with zipfile.ZipFile(archive_path) as archive:
            licenses = extract_component(
                archive, "info", output, license_dir, target_platform
            )
            extract_component(archive, "pkg", output, license_dir, target_platform)
        inventory.append(
            {
                "name": package["name"],
                "version": package["version"],
                "license": package["license"],
                "sha256": package["sha256"],
                "url": f"{CHANNEL}/{package['subdir']}/{package['filename']}",
                "license_files": sorted(licenses),
            }
        )
    (output / "licenses/conda").mkdir(parents=True, exist_ok=True)
    (output / "licenses/conda/packages.json").write_text(
        json.dumps(inventory, indent=2) + "\n"
    )
    gdal = {
        "win-64": "gdal.dll",
        "osx-64": "libgdal.dylib",
        "osx-arm64": "libgdal.dylib",
    }.get(target_platform, "libgdal.so")
    for required in (
        output / "lib" / gdal,
        output / "share/gdal",
        output / "share/proj/proj.db",
        output / "share/cacert.pem",
    ):
        if not required.exists():
            raise RuntimeError(f"native GDAL bundle is incomplete: {required}")


if __name__ == "__main__":
    main()
