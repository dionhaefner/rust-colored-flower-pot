"""Fetch the checksum-pinned Terracotta release source used as the oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
from urllib.request import urlopen

VERSION = "0.10.1"
FILENAME = f"terracotta-{VERSION}.tar.gz"
SHA256 = "e5d2ac9b78a36d0f28414b1a4890c961dede50d1f9ce33b8d23eee643f5e2348"
METADATA_URL = f"https://pypi.org/pypi/terracotta/{VERSION}/json"


def release_bytes() -> bytes:
    with urlopen(METADATA_URL, timeout=30) as response:
        metadata = json.load(response)
    releases = [item for item in metadata["urls"]
                if item["filename"] == FILENAME and item["packagetype"] == "sdist"]
    if len(releases) != 1 or releases[0]["digests"]["sha256"] != SHA256:
        raise RuntimeError("PyPI does not list the pinned Terracotta source archive")
    with urlopen(releases[0]["url"], timeout=60) as response:
        return response.read()


def fetch(output: Path, archive: Path | None = None) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to replace existing reference: {output}")
    payload = archive.read_bytes() if archive is not None else release_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != SHA256:
        raise ValueError(f"Terracotta source SHA-256 mismatch: {digest}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="terracotta-reference-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        compressed = temporary / FILENAME
        compressed.write_bytes(payload)
        unpacked = temporary / "unpacked"
        unpacked.mkdir()
        with tarfile.open(compressed, "r:gz") as source:
            source.extractall(unpacked, filter="data")
        root = unpacked / f"terracotta-{VERSION}"
        if not (root / "terracotta/__init__.py").is_file() or not (root / "tests/data/db-v075.sqlite").is_file():
            raise RuntimeError("pinned Terracotta source is missing required package or test files")
        root.rename(output)
        (output / "source.tar.gz").write_bytes(payload)
    print(f"Terracotta {VERSION} reference: {output} (SHA-256 {digest})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="use a locally cached pinned sdist")
    arguments = parser.parse_args()
    fetch(arguments.output.resolve(), arguments.archive)
