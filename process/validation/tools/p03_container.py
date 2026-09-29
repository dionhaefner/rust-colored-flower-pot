"""Build the P03 container and compare an in-container real-raster render."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import numpy as np
from PIL import Image

from common import ROOT, write_json


def decoded(png):
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def run(bundle, out, tag):
    if out.exists():
        raise FileExistsError("choose a fresh P03 container output directory")
    if subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True
    ).returncode == 0:
        raise RuntimeError(f"refusing to replace existing Docker image tag {tag}")
    context = out / "context"
    fixture = context / "fixture"
    fixture.mkdir(parents=True)
    shutil.copytree(bundle, context / "bundle")
    shutil.copy2(ROOT / "packaging/container/Dockerfile", context / "Dockerfile")
    raster = ROOT / ".cache/corpus/subsets/lagoon-red.tif"
    shutil.copy2(raster, fixture / "raster.tif")
    database = fixture / "terracotta.sqlite"
    shutil.copy2(ROOT / "process/validation/artifacts/capture-reproduction/reference.sqlite", database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE datasets SET path = '/data/raster.tif'")
    event = {
        "version": "2.0",
        "rawPath": "/singleband/lagoon/red/preview.png",
        "rawQueryString": "stretch_range=%5B0%2C3000%5D",
        "requestContext": {"http": {"method": "GET"}},
    }
    write_json(fixture / "event.json", event)

    build = subprocess.run(
        ["docker", "build", "--pull", "--tag", tag, str(context)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    invocation = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none", tag,
            "lambda-event", "-d", "/data/terracotta.sqlite", "--event", "/data/event.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    response = json.loads(invocation.stdout)
    png = base64.b64decode(response["body"])
    (out / "container.png").write_bytes(png)
    golden = (ROOT / "process/validation/reference/capture/images/lagoon-red-preview.png").read_bytes()
    exact = bool(np.array_equal(decoded(png), decoded(golden)))
    inspect = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", tag],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    )[0]
    report = {
        "passed": response["statusCode"] == 200 and response["isBase64Encoded"] and exact,
        "image_tag": tag,
        "image_id": inspect["Id"],
        "image_os": inspect["Os"],
        "image_architecture": inspect["Architecture"],
        "image_size_bytes": inspect["Size"],
        "base_image": "debian:bookworm-slim",
        "network_at_runtime": "none",
        "decoded_rgba_exact": exact,
        "response_status": response["statusCode"],
        "png_sha256": hashlib.sha256(png).hexdigest(),
        "build_stdout_tail": build.stdout[-2000:],
        "build_stderr_tail": build.stderr[-2000:],
        "claim": "The relocatable Linux x86_64 bundle loads in a clean Debian container and reads/renders the pinned real Sentinel-2 subset without the reference venv.",
    }
    write_json(out / "report.json", report)
    shutil.rmtree(context)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "process/validation/artifacts/p03-bundle")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tag", default="terracotta-p03-local:20260922-2")
    args = parser.parse_args()
    run(args.bundle.resolve(), args.out.resolve(), args.tag)
