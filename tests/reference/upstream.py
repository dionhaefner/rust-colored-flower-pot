"""Check the pinned Terracotta release test baseline and classify known failures."""

from __future__ import annotations

import argparse
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_FAILURES = ["test_logging_visible[debug]", "test_logging_visible[info]"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--mysql-server", required=True)
    parser.add_argument("--postgresql-server", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    reference_python = args.reference_python.absolute()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    junit = out / "junit.xml"
    command = [
        str(reference_python),
        "-m",
        "pytest",
        ".cache/reference/tests",
        "--ignore=.cache/reference/tests/drivers/test_sqlite_remote.py",
        "--mysql-server",
        args.mysql_server,
        "--postgresql-server",
        args.postgresql_server,
        f"--junitxml={junit}",
    ]
    with (out / "pytest.log").open("w") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=log, check=False)
    if not junit.is_file():
        raise RuntimeError("reference suite did not write JUnit results")
    cases = list(ET.parse(junit).iter("testcase"))
    failures = sorted(
        row.attrib["name"] for row in cases if row.find("failure") is not None
    )
    errors = [row.attrib["name"] for row in cases if row.find("error") is not None]
    skipped = [row.attrib["name"] for row in cases if row.find("skipped") is not None]
    accepted = (
        result.returncode == 1
        and len(cases) == 528
        and failures == EXPECTED_FAILURES
        and not errors
        and not skipped
    )
    report = {
        "accepted": accepted,
        "cases": len(cases),
        "passed": len(cases) - len(failures) - len(errors) - len(skipped),
        "known_failures": failures,
        "errors": errors,
        "skipped": skipped,
        "exit_code": result.returncode,
        "excluded_module": "tests/drivers/test_sqlite_remote.py",
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if not accepted:
        raise AssertionError("reference release suite changed; inspect pytest.log")


if __name__ == "__main__":
    main()
