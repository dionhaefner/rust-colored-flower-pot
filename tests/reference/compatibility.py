"""Replay supported public reference tests through an installed RCFP wheel."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import shutil
import subprocess
import tokenize
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    "drivers/test_drivers.py": [
        "test_auto_detect",
        "test_creation",
        "test_creation_descriptions",
        "test_creation_invalid",
        "test_creation_invalid_description",
        "test_creation_reserved_names",
        "test_connect_before_create",
        "test_invalid_key_types",
        "test_parse_connection_string_with_invalid_schemes",
        "test_get_driver_invalid",
    ],
    "drivers/test_raster_drivers.py": [
        "test_insertion_and_retrieval",
        "test_path_override",
        "test_where",
        "test_where_with_multiquery",
        "test_pagination",
        "test_lazy_loading",
        "test_precomputed_metadata",
        "test_wrong_key_number",
        "test_insertion_invalid_raster",
        "test_raster_retrieval",
        "test_raster_duplicate",
        "test_deletion",
        "test_delete_nonexisting",
        "test_nodata_consistency",
        "test_float_precision",
    ],
}
EXPECTED_CASES = 28


def select_tests(source, names):
    lines = source.splitlines(keepends=True)
    nodes = ast.parse(source).body
    missing = set(names) - {
        node.name for node in nodes if isinstance(node, ast.FunctionDef)
    }
    if missing:
        raise ValueError(f"Missing required public tests: {sorted(missing)}")
    for node in reversed(nodes):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")
            and node.name not in names
        ):
            start = (
                min([node.lineno, *(item.lineno for item in node.decorator_list)]) - 1
            )
            del lines[start : node.end_lineno]
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-python", type=Path, required=True)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    tests = out / "tests"
    tests.mkdir()
    source = ROOT / ".cache/reference/tests"
    shutil.copy2(source / "conftest.py", tests)
    for name, required in MODULES.items():
        path = source / name
        target = tests / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(select_tests(path.read_text(), required))
    for path in tests.rglob("*.py"):
        tokens = []
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type == tokenize.NAME and token.string == "terracotta":
                token = token._replace(string="rcfp")
            elif token.type == tokenize.STRING:
                token = token._replace(
                    string=token.string.replace("terracotta.", "rcfp.")
                )
            tokens.append(token)
        path.write_text(tokenize.untokenize(tokens))
    env = dict(os.environ, RCFP_COMPAT_PROFILE="terracotta-0.10.1")
    package = subprocess.check_output(
        [
            str(args.candidate_python.absolute()),
            "-I",
            "-c",
            "import rcfp; print(rcfp.__file__)",
        ],
        env=env,
        text=True,
    ).strip()
    env["PYTHONPATH"] = str(Path(package).parent.parent)
    junit = out / "junit.xml"
    command = [
        str(args.reference_python.absolute()),
        "-m",
        "pytest",
        *[str(tests / name) for name in MODULES],
        "-q",
        "-k",
        "sqlite or test_get_driver_invalid or test_parse_connection_string_with_invalid_schemes",
        f"--junitxml={junit}",
    ]
    with (out / "pytest.log").open("w") as log:
        result = subprocess.run(
            command, cwd=ROOT, env=env, stdout=log, stderr=log, check=False
        )
    cases = list(ET.parse(junit).iter("testcase")) if junit.is_file() else []
    passed = (
        result.returncode == 0
        and len(cases) == EXPECTED_CASES
        and all(
            row.find("failure") is None
            and row.find("error") is None
            and row.find("skipped") is None
            for row in cases
        )
    )
    report = {
        "passed": passed,
        "cases": len(cases),
        "required_tests": MODULES,
        "scope": "Documented driver workflows; SQL providers use the separate live roundtrip lane.",
        "exit_code": result.returncode,
        "package": package,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
