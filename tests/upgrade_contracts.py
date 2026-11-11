"""Rehearse a compatibility-profile SQLite upgrade and restore on exact wheels."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/corpus"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def worker(args):
    import rcfp
    from PIL import Image

    require(rcfp.compatibility_profile == "terracotta", "profile mismatch")
    package = Path(rcfp.__file__).resolve().parent
    require(
        package.is_relative_to(Path(sys.prefix).resolve()),
        "Use an installed wheel environment",
    )
    with zipfile.ZipFile(args.wheel) as wheel:
        files = [
            name
            for name in wheel.namelist()
            if name.startswith("rcfp/") or name == "rcfp_cli.py"
        ]
        require(bool(files), "wheel has no package")
        for name in files:
            if name.endswith("/"):
                continue
            installed = package.parent / name
            require(
                installed.is_file()
                and digest(installed) == hashlib.sha256(wheel.read(name)).hexdigest(),
                f"Installed file differs from wheel: {name}",
            )
    identity = {
        "version": rcfp.__version__,
        "wheel_sha256": digest(args.wheel),
        "core_sha256": digest(package / "runtime/lib/librcfp_core.so")
        if sys.platform.startswith("linux")
        else None,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    if args.worker == "identity":
        print(json.dumps(identity))
        return
    driver = rcfp.get_driver(str(args.database))
    names = [
        row["id"]
        for row in json.loads((CORPUS / "manifest.json").read_text())["fixtures"]
    ]
    if args.worker == "create":
        driver.create(["scene", "band"])
        for name in names:
            driver.insert(["application", name], str(CORPUS / f"{name}.tif"))
    elif args.worker == "write":
        driver.insert(["application", "copy"], str(CORPUS / "red.tif"))
    datasets = sorted([list(key) for key in driver.get_datasets()])
    metadata = {name: driver.get_metadata(["application", name]) for name in names}
    single = "/singleband/application/"
    rgb = "/rgb/application/preview.png?" + urlencode(
        {
            "r": "red",
            "g": "green",
            "b": "blue",
            "r_range": "[0,3000]",
            "g_range": "[0,3000]",
            "b_range": "[0,3000]",
        }
    )
    palette = {
        value: [value, 255 - value, 100, 255]
        for value in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
    }
    requests = {
        "singleband": single + "red/preview.png?stretch_range=[0,3000]",
        "mask-edge": single + "edge/preview.png?stretch_range=[0,3000]",
        "geographic-elevation": single + "elevation/preview.png",
        "categories": single
        + "classes/preview.png?"
        + urlencode(
            {"colormap": "explicit", "explicit_color_map": json.dumps(palette)}
        ),
        "rgb": rgb,
        "expression": "/compute/application/preview.png?"
        + urlencode(
            {
                "expression": "(v1-v2)/(v1+v2)",
                "v1": "nir",
                "v2": "red",
                "stretch_range": "[-1,1]",
            }
        ),
    }
    images = {}
    for name, url in requests.items():
        status, content_type, body, _ = rcfp.request(str(args.database), "GET", url)
        require(
            status == 200 and content_type == "image/png",
            f"{name}: {status} {body[:200]!r}",
        )
        image = Image.open(io.BytesIO(body)).convert("RGBA")
        require(
            len(image.getcolors(maxcolors=image.width * image.height)) > 1,
            f"{name}: empty or constant image",
        )
        images[name] = {
            "size": list(image.size),
            "rgba_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        }
    print(
        json.dumps(
            {
                "identity": identity,
                "datasets": datasets,
                "metadata": metadata,
                "images": images,
            }
        )
    )


def main(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "checker_sha256": digest(Path(__file__)),
        "cases": [],
        "artifacts": {},
        "exclusions": [
            "Independent adoption and production retention",
            "Native catalog/schema migration",
            "Remote SQL/credentials",
            "Cloud/platform qualification",
            "Production latency and cost",
        ],
        "skips": [],
    }
    inputs = {
        row["file"]: row["sha256"]
        for row in json.loads((CORPUS / "manifest.json").read_text())["fixtures"]
    }
    report["inputs"] = inputs
    database = out / "trial.sqlite"
    backup = out / "backup.sqlite"
    restore = out / "restored.sqlite"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TC_", "RCFP_"))
        and key
        not in (
            "PYTHONPATH",
            "TERRACOTTA_GDAL_LIBRARY",
            "GDAL_DATA",
            "PROJ_DATA",
            "LD_LIBRARY_PATH",
        )
    }
    environment.update(
        RCFP_COMPAT_PROFILE="terracotta",
        TC_RUST_MERCATOR_FAST="0",
        TC_RUST_UTM_FAST="0",
        TC_PREFETCH_TILE_RING="0",
    )

    def capture(label, python, wheel, mode, path=database):
        command = [
            str(python.absolute()),
            str(Path(__file__).resolve()),
            "--worker",
            mode,
            "--wheel",
            str(wheel.resolve()),
            "--database",
            str(path),
        ]
        result = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        (out / f"{label}.stdout").write_text(result.stdout)
        (out / f"{label}.stderr").write_text(result.stderr)
        report.setdefault("commands", []).append(
            {"command": command, "returncode": result.returncode}
        )
        require(
            result.returncode == 0,
            f"{label}: exit {result.returncode}; see retained stderr",
        )
        return json.loads(result.stdout)

    label = "artifact-identity"
    started = time.monotonic()
    try:
        for name, python, wheel in (
            ("previous", args.previous_python, args.previous_wheel),
            ("candidate", args.candidate_python, args.candidate_wheel),
        ):
            report["artifacts"][name] = capture(name, python, wheel, "identity")
        require(
            report["artifacts"]["previous"]["wheel_sha256"]
            != report["artifacts"]["candidate"]["wheel_sha256"],
            "Upgrade needs two distinct artifacts",
        )
        report["cases"].append({"case": label, "status": "passed"})
        label = "previous-application-baseline"
        require(
            all(digest(CORPUS / name) == value for name, value in inputs.items()),
            "fixture integrity",
        )
        baseline = capture(
            "baseline", args.previous_python, args.previous_wheel, "create"
        )
        report["cases"].append({"case": label, "status": "passed"})
        label = "committed-wal-backup"
        with sqlite3.connect(database) as source:
            require(
                source.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",),
                "WAL unavailable",
            )
            source.execute("CREATE TABLE application_probe(value TEXT)")
            source.execute(
                "INSERT INTO application_probe VALUES ('committed WAL data')"
            )
            source.commit()
            require(
                Path(str(database) + "-wal").stat().st_size > 0,
                "WAL has no committed data",
            )
            with sqlite3.connect(backup) as destination:
                source.backup(destination)
        backup_hash = digest(backup)
        report["cases"].append({"case": label, "status": "passed"})
        label = "candidate-upgrade-read"
        candidate = capture(
            "upgrade", args.candidate_python, args.candidate_wheel, "read"
        )
        for field in ("datasets", "metadata", "images"):
            require(candidate[field] == baseline[field], f"upgrade changed {field}")
        report["cases"].append({"case": label, "status": "passed"})
        label = "candidate-write-previous-read"
        written = capture(
            "candidate-write", args.candidate_python, args.candidate_wheel, "write"
        )
        rolled_back = capture(
            "previous-restart", args.previous_python, args.previous_wheel, "read"
        )
        require(
            written["datasets"]
            == sorted(baseline["datasets"] + [["application", "copy"]]),
            "candidate write missing",
        )
        for field in ("datasets", "metadata", "images"):
            require(
                rolled_back[field] == written[field],
                f"previous restart changed {field}",
            )
        report["cases"].append({"case": label, "status": "passed"})
        label = "backup-restore-previous-restart"
        with (
            sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True) as source,
            sqlite3.connect(restore) as destination,
        ):
            source.backup(destination)
            require(
                destination.execute("PRAGMA integrity_check").fetchone() == ("ok",),
                "restore integrity",
            )
            require(
                destination.execute("SELECT value FROM application_probe").fetchall()
                == [("committed WAL data",)],
                "restore lost committed WAL data",
            )
        restored = capture(
            "restore", args.previous_python, args.previous_wheel, "read", restore
        )
        for field in ("datasets", "metadata", "images"):
            require(restored[field] == baseline[field], f"restore changed {field}")
        require(digest(backup) == backup_hash, "backup changed")
        require(
            all(digest(CORPUS / name) == value for name, value in inputs.items()),
            "rasters changed",
        )
        report["backup_sha256"] = backup_hash
        report["cases"].append({"case": label, "status": "passed"})
    except Exception as error:  # noqa: BLE001 - retain failed stage and completed evidence
        report["cases"].append(
            {"case": label, "status": "failed", "error": repr(error)}
        )
    report["automated_elapsed_seconds"] = time.monotonic() - started
    report["passed"] = all(row["status"] == "passed" for row in report["cases"])
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-python", type=Path)
    parser.add_argument("--previous-wheel", type=Path)
    parser.add_argument("--candidate-python", type=Path)
    parser.add_argument("--candidate-wheel", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--worker",
        choices=("identity", "create", "read", "write"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--wheel", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    needed = (
        ("wheel", "database")
        if args.worker
        else (
            "previous_python",
            "previous_wheel",
            "candidate_python",
            "candidate_wheel",
            "out",
        )
    )
    for name in needed:
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    worker(args) if args.worker else main(args)
