"""Compare installed compatibility drivers with Terracotta on live SQL engines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa

PROFILE = "terracotta"
SCRIPT = Path(__file__).with_name("compatibility_contracts.py").resolve()


def worker(python, package, database, output, create=False):
    # The original driver does not accept RCFP's explicit loopback TLS selector.
    if package == "terracotta":
        parts = urlsplit(database)
        database = urlunsplit(parts._replace(query=""))
    command = [
        str(python),
        str(SCRIPT),
        "--worker",
        package,
        "--database",
        database,
        "--output",
        str(output),
    ]
    if create:
        command.append("--create")
    result = subprocess.run(
        command,
        env=dict(os.environ, RCFP_COMPAT_PROFILE=PROFILE),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(output.read_text())


def compare(expected, actual):
    assert expected == actual, (
        f"changed fields: {[key for key in expected if expected[key] != actual.get(key)]}"
    )


def check_connection_cache(database):
    script = """
import sys
from urllib.parse import urlsplit, urlunsplit
from rcfp import get_driver

first = get_driver(sys.argv[1])
first.get_keys()
parts = urlsplit(sys.argv[1])
changed = urlunsplit(parts._replace(query="sslmode=require"))
second = get_driver(changed)
assert second is not first, "changed TLS configuration reused a SQL driver"
try:
    second.get_keys()
except Exception as error:
    assert "verified TLS" in str(error) or "verify-full" in str(error), str(error)
else:
    raise AssertionError("changed TLS configuration reused an open SQL connection")
assert first.get_keys(), "rejected connection invalidated the original driver"
"""
    result = subprocess.run(
        [sys.executable, "-c", script, database],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def run_provider(name, base, reference, folder):
    parts = urlsplit(base)
    assert parts.hostname in ("127.0.0.1", "::1"), (
        "SQL tests require disposable loopback servers"
    )
    suffix = uuid.uuid4().hex[:16]
    databases = [
        f"rcfp_compat_{suffix}_{origin}" for origin in ("reference", "candidate")
    ]
    admin = sa.engine.make_url(base).set(
        drivername="mysql+pymysql" if name == "mysql" else "postgresql+psycopg2",
        database=None if name == "mysql" else "postgres",
    )
    if name == "mysql":
        admin = admin.difference_update_query(["sslmode"])
    engine = sa.create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        for origin, database_name in zip(("terracotta", "rcfp"), databases):
            database = urlunsplit(
                parts._replace(path=f"/{database_name}", query="sslmode=disable")
            )
            worker(
                reference if origin == "terracotta" else sys.executable,
                origin,
                database,
                folder / f"{origin}-create.json",
                create=True,
            )
            # Reopen both implementations so metadata caches cannot conceal writes.
            expected = worker(
                reference, "terracotta", database, folder / f"{origin}-reference.json"
            )
            actual = worker(
                sys.executable, "rcfp", database, folder / f"{origin}-candidate.json"
            )
            compare(expected, actual)
            check_connection_cache(database)
    finally:
        with engine.connect() as connection:
            for database_name in databases:
                connection.execute(sa.text(f"DROP DATABASE IF EXISTS {database_name}"))
        engine.dispose()


def main():
    import rcfp

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    assert rcfp.compatibility_profile == PROFILE
    assert Path(rcfp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    args.out.mkdir(parents=True, exist_ok=False)
    cases = []
    for name, variable in (
        ("mysql", "RCFP_TEST_MYSQL_URL"),
        ("postgresql", "RCFP_TEST_POSTGRESQL_URL"),
    ):
        folder = args.out / name
        folder.mkdir()
        try:
            run_provider(name, os.environ[variable], args.reference_python, folder)
        except Exception as error:  # noqa: BLE001
            cases.append({"id": name, "passed": False, "error": repr(error)})
        else:
            cases.append(
                {"id": name, "passed": True, "driver_cache_configuration": True}
            )
    report = {
        "profile": PROFILE,
        "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
        "cases": cases,
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if not all(row["passed"] for row in cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
