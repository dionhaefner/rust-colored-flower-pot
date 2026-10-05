"""Acquire the checksum-pinned full scenes used by navigation measurements."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen

from navigation_benchmark import ROOT, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/fixtures/navigation.json"
    )
    parser.add_argument("--destination", type=Path, default=ROOT / ".cache/corpus")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    for name, row in manifest["sources"].items():
        destination = args.destination / row["file"]
        if destination.is_file():
            if digest(destination) != row["sha256"]:
                raise ValueError(f"checksum mismatch: {name}")
        else:
            if args.offline:
                raise FileNotFoundError(destination)
            with tempfile.NamedTemporaryFile(
                dir=args.destination, delete=False
            ) as temporary:
                staging = Path(temporary.name)
                try:
                    with urlopen(row["url"], timeout=120) as source:
                        shutil.copyfileobj(source, temporary)
                    temporary.flush()
                    if digest(staging) != row["sha256"]:
                        raise ValueError(f"download checksum mismatch: {name}")
                    staging.replace(destination)
                finally:
                    staging.unlink(missing_ok=True)
        print(f"verified: {name}", flush=True)


if __name__ == "__main__":
    main()
