"""Retain shared-core and embedded-asset notices in every native runtime."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICES = {
    "LICENSE": "TERRACOTTA-LICENSE.txt",
    "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
    "src/assets/COLOR_OPERATIONS_LICENSE.txt": "COLOR_OPERATIONS_LICENSE.txt",
    "src/assets/CRICK_LICENSE.txt": "CRICK_LICENSE.txt",
    "src/assets/TDIGEST_LICENSE.txt": "TDIGEST_LICENSE.txt",
    "src/assets/static/redoc.standalone.js.LICENSE.txt": "REDOC-LICENSE.txt",
    "packaging/licenses/CLIENT-LICENSE.txt": "CLIENT-LICENSE.txt",
}


def stage(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for source, name in NOTICES.items():
        shutil.copy2(ROOT / source, output / name)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging/collect-licenses.py"),
            str(output / "cargo"),
        ],
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    stage(parser.parse_args().output)
