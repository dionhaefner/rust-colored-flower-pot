"""Validate RCFP binary wheel contents and metadata."""

from __future__ import annotations

import argparse
import base64
import csv
import io
import email
import hashlib
import json
import zipfile
from pathlib import Path


def verify(path: Path, version: str | None = None) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        if version is not None:
            assert path.name.startswith(f"rcfp-{version}-"), "wheel filename/version mismatch"
        names = archive.namelist()
        files = {name for name in names if not name.endswith("/")}
        assert len(names) == len(set(names)), "duplicate wheel members"
        records = [name for name in files if name.endswith(".dist-info/RECORD")]
        assert len(records) == 1, "wheel RECORD missing or duplicated"
        rows = list(csv.reader(io.StringIO(archive.read(records[0]).decode())))
        assert {row[0] for row in rows} == files, "wheel RECORD does not enumerate exact members"
        for name, digest, size in rows:
            data = archive.read(name)
            if name == records[0]:
                assert not digest and not size, "RECORD self-hash must be blank"
                continue
            assert size == str(len(data)), f"incorrect RECORD size: {name}"
            algorithm, encoded = digest.split("=", 1)
            assert algorithm == "sha256", f"unexpected RECORD digest: {name}"
            actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            assert actual == encoded, f"incorrect RECORD digest: {name}"
        metadata_name = next(name for name in files if name.endswith(".dist-info/METADATA"))
        wheel_name = next(name for name in files if name.endswith(".dist-info/WHEEL"))
        metadata = email.message_from_bytes(archive.read(metadata_name))
        wheel = email.message_from_bytes(archive.read(wheel_name))
        assert metadata["Name"].lower() == "rcfp", metadata["Name"]
        assert set(metadata["Requires-Python"].split(",")) == {">=3.12", "<3.13"}, metadata["Requires-Python"]
        if version is not None:
            assert metadata["Version"] == version, (metadata["Version"], version)
        tags = wheel.get_all("Tag") or []
        assert tags and all("-none-any" not in tag for tag in tags), tags
        assert not any("-linux_" in tag for tag in tags), "unrepaired Linux wheel"
        runtime = "rcfp/runtime/"
        assert any(name.startswith(runtime + "lib/") and "gdal" in Path(name).name.lower()
                   for name in files), "GDAL library missing"
        assert any(name.startswith(runtime + "lib/") and "rcfp_core" in name
                   for name in files), "Rust core library missing"
        assert runtime + "share/proj/proj.db" in files, "PROJ database missing"
        assert any(name.startswith(runtime + "share/gdal/") for name in files), "GDAL data missing"
        assert any(name.startswith("rcfp/_native") for name in files), "Python extension missing"
        assert runtime + "bin/rcfp" in files or runtime + "bin/rcfp.exe" in files, "RCFP CLI missing"
        assert not any("rasterio" in item.lower() for item in metadata.get_all("Requires-Dist", [])), "unexpected Rasterio runtime dependency"
    return {"wheel": path.name, "version": metadata["Version"], "tags": tags,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--version")
    args = parser.parse_args()
    print(json.dumps(verify(args.wheel, args.version), indent=2))


if __name__ == "__main__":
    main()
