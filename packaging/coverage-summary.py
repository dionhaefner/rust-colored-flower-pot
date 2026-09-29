"""Render source coverage from CI artifacts as a GitHub job summary."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def ratio(covered: int, total: int) -> str:
    if total <= 0:
        raise ValueError("coverage report has no executable lines")
    return f"{covered}/{total} ({covered / total:.1%})"


def rust_lines(path: Path) -> dict[str, tuple[int, int]]:
    root = Path.cwd().resolve()
    hits: dict[str, dict[int, int]] = defaultdict(dict)
    source: str | None = None
    for row in path.read_text().splitlines():
        if row.startswith("SF:"):
            filename = Path(row[3:])
            if not filename.is_absolute():
                filename = root / filename
            try:
                relative = filename.resolve().relative_to(root)
            except ValueError:
                source = None
                continue
            source = relative.as_posix() if relative.parts[0] == "src" and relative.suffix == ".rs" else None
        elif row.startswith("DA:") and source is not None:
            line, count = row[3:].split(",", 2)[:2]
            number = int(line)
            hits[source][number] = max(hits[source].get(number, 0), int(count))
        elif row == "end_of_record":
            source = None
    return {
        name: (sum(count > 0 for count in lines.values()), len(lines))
        for name, lines in hits.items()
    }


def python_lines(path: Path) -> tuple[dict[str, tuple[int, int]], str | None]:
    report = json.loads(path.read_text())
    files = {}
    branch_covered = 0
    branch_total = 0
    for name, values in report["files"].items():
        if "rcfp" not in name.replace("\\", "/").split("/"):
            continue
        summary = values["summary"]
        files[name] = (summary["covered_lines"], summary["num_statements"])
        branch_covered += summary.get("covered_branches", 0)
        branch_total += summary.get("num_branches", 0)
    branches = ratio(branch_covered, branch_total) if branch_total else None
    return files, branches


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("language", choices=("rust", "python"))
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.language == "rust":
        files = rust_lines(args.report)
        branches = None
        title = "Rust source coverage"
        scope = "Rust unit and SQL/remote integration tests plus native catalog operations on Linux x86_64."
    else:
        files, branches = python_lines(args.report)
        title = "Python adapter coverage"
        scope = "Installed wheel contract suite on Linux x86_64, CPython 3.12; in-process `rcfp` Python code only."
    if not files:
        raise ValueError(f"no {args.language} product source files found in {args.report}")
    covered = sum(values[0] for values in files.values())
    total = sum(values[1] for values in files.values())
    print(f"### {title}\n")
    print(f"{scope}\n")
    print(f"**Line coverage:** {ratio(covered, total)} across {len(files)} files.")
    if branches is not None:
        print(f"**Python branch coverage:** {branches}.")
    print("\nLowest covered files with at least 10 executable lines:\n")
    print("| File | Lines covered |")
    print("| --- | ---: |")
    ranked = sorted(
        ((name, counts) for name, counts in files.items() if counts[1] >= 10),
        key=lambda item: (item[1][0] / item[1][1], item[0]),
    )
    for name, counts in ranked[:10]:
        print(f"| `{name}` | {ratio(*counts)} |")
    print()


if __name__ == "__main__":
    main()
