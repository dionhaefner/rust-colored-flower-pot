"""Back up a SQLite catalog, including committed WAL data."""

import argparse
import sqlite3
from pathlib import Path


def backup(source: Path, destination: Path):
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Choose a new backup path: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original,
        sqlite3.connect(destination) as copy,
    ):
        original.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("Backup integrity check failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
