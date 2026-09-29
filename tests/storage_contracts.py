"""Exercise the native catalog and HTTP server against disposable SQL engines."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RASTER = ROOT / "tests/fixtures/corpus/red.tif"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def invoke(binary, folder, request):
    instruction = folder / "operation.json"
    instruction.write_text(json.dumps(request))
    result = subprocess.run(
        [str(binary), "data-operation", str(instruction)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr.strip())
    return json.loads(result.stdout)


def fetch(binary, database, path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [str(binary), "serve", "-d", database, "--bind", f"127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/keys", timeout=1
                ).close()
                break
            except (urllib.error.URLError, TimeoutError):
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode(errors="replace"))
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start")
        try:
            response = urllib.request.urlopen(
                f"http://127.0.0.1:{port}" + path, timeout=30
            )
        except urllib.error.HTTPError as error:
            response = error
        return response.status, response.headers.get_content_type(), response.read()
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def exercise(binary, folder, base, name):
    database = (
        str(folder / "control.sqlite")
        if name == "sqlite"
        else base.rstrip("/") + "/rcfp_ci_" + uuid4().hex[:12]
    )
    prefix = {"path": database}
    invoke(
        binary,
        folder,
        {
            **prefix,
            "operation": "create",
            "keys": [
                {"key": "scene", "description": "Location"},
                {"key": "band", "description": "Band"},
            ],
        },
    )
    for band in ("red", "green"):
        invoke(
            binary,
            folder,
            {
                **prefix,
                "operation": "insert",
                "keys": ["lagoon", band],
                "raster": str(RASTER),
            },
        )
    status, kind, body = fetch(binary, database, "/keys")
    require(status == 200 and kind == "application/json", "keys endpoint failed")
    require(
        [row["key"] for row in json.loads(body)["keys"]] == ["scene", "band"],
        "key order changed",
    )
    status, kind, body = fetch(binary, database, "/datasets?scene=lagoon")
    require(status == 200 and kind == "application/json", "datasets endpoint failed")
    require(
        {row["band"] for row in json.loads(body)["datasets"]} == {"red", "green"},
        "SQL dataset rows wrong",
    )
    status, kind, body = fetch(binary, database, "/metadata/lagoon/red")
    require(status == 200 and kind == "application/json", "metadata endpoint failed")
    require(
        json.loads(body)["keys"] == {"scene": "lagoon", "band": "red"},
        "SQL metadata keys wrong",
    )
    status, kind, body = fetch(
        binary,
        database,
        "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D",
    )
    require(status == 200 and kind == "image/png", "SQL-backed raster failed")
    with Image.open(io.BytesIO(body)) as image:
        pixels = image.convert("RGBA").tobytes()
        require(image.size == (256, 256), "SQL-backed image size wrong")
    try:
        invoke(
            binary,
            folder,
            {
                **prefix,
                "operation": "batch",
                "operations": [
                    {"operation": "delete", "keys": ["lagoon", "red"]},
                    {"operation": "insert", "keys": ["bad"], "raster": str(RASTER)},
                ],
            },
        )
    except AssertionError:
        pass
    else:
        raise AssertionError("invalid batch did not fail")
    status, _, _ = fetch(binary, database, "/metadata/lagoon/red")
    require(status == 200, "failed batch deleted committed row")
    invoke(
        binary, folder, {**prefix, "operation": "delete", "keys": ["lagoon", "green"]}
    )
    status, _, _ = fetch(binary, database, "/metadata/lagoon/green")
    require(status == 404, "deleted row remains visible")
    invoke(
        binary,
        folder,
        {
            **prefix,
            "operation": "insert",
            "keys": ["lagoon", "green"],
            "raster": str(RASTER),
        },
    )
    return {
        "engine": name,
        "database": database,
        "image_sha256": hashlib.sha256(pixels).hexdigest(),
        "cases": 10,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sqlite-only", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    manifest = json.loads((ROOT / "tests/fixtures/corpus/manifest.json").read_text())
    expected = next(row for row in manifest["fixtures"] if row["id"] == "red")
    require(
        hashlib.sha256(RASTER.read_bytes()).hexdigest() == expected["sha256"],
        "raster fixture changed",
    )
    targets = [("sqlite", "")]
    if not args.sqlite_only:
        targets += [
            ("mysql", os.environ["RCFP_TEST_MYSQL_URL"]),
            ("postgresql", os.environ["RCFP_TEST_POSTGRESQL_URL"]),
        ]
    rows = []
    for name, base in targets:
        folder = out / name
        folder.mkdir()
        rows.append(exercise(binary, folder, base, name))
    require(
        len({row["image_sha256"] for row in rows}) == 1, "backend changed decoded image"
    )
    report = {
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "results": rows,
        "total_cases": sum(row["cases"] for row in rows) + 1,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"engines": [row["engine"] for row in rows], "cases": report["total_cases"]}
        )
    )


if __name__ == "__main__":
    main()
