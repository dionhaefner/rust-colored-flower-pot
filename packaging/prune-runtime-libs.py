"""Keep the macOS shared libraries reachable from the packaged GDAL runtime."""

from __future__ import annotations

import argparse
import subprocess
from collections import deque
from pathlib import Path


def dependencies(path: Path) -> list[str]:
    result = subprocess.run(
        ["otool", "-L", str(path)], capture_output=True, text=True, check=True
    )
    return [line.strip().split(" (", 1)[0] for line in result.stdout.splitlines()[1:]]


def prune(runtime: Path) -> None:
    library_dir = runtime / "lib"
    libraries = sorted(library_dir.glob("*.dylib"))
    by_name = {path.name: path.resolve() for path in libraries}
    roots = [runtime / "bin" / "rcfp", library_dir / "librcfp_core.dylib"]
    gdal = by_name.get("libgdal.dylib")
    if gdal is None:
        raise RuntimeError("GDAL library is missing")
    pending = deque([*roots, gdal])
    reachable = set()
    while pending:
        path = pending.popleft().resolve()
        if path in reachable:
            continue
        reachable.add(path)
        for dependency in dependencies(path):
            name = Path(dependency).name
            target = by_name.get(name)
            if target is not None:
                pending.append(target)
            elif "/conda-bld/" in dependency or dependency.startswith("@rpath/"):
                raise RuntimeError(f"required bundled library is missing: {dependency}")
    removed = []
    for path in libraries:
        if path.resolve() not in reachable:
            path.unlink()
            removed.append(path.name)
    print(
        f"macOS runtime closure: kept {len(libraries) - len(removed)} libraries; removed {len(removed)} unused libraries"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    args = parser.parse_args()
    prune(args.runtime.resolve())


if __name__ == "__main__":
    main()
