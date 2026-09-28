"""Load and render with the Lambda zip in the pinned official AL2023 image."""

import argparse
import base64
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import zipfile

import numpy as np
from PIL import Image

from common import ROOT, write_json


DEFAULT_IMAGE = "public.ecr.aws/lambda/provided@sha256:6e44223227dfb00686065ab66db7c4ce86f1483a8f69e40bcba5ebdb0f4d5fd7"


def decoded(png):
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def run(lambda_zip, bundle, out, image):
    if out.exists():
        raise FileExistsError("choose a fresh P03 Lambda-runtime output directory")
    package = out / "package"
    fixture = out / "fixture"
    package.mkdir(parents=True)
    fixture.mkdir()
    with zipfile.ZipFile(lambda_zip) as archive:
        archive.extractall(package)
        for info in archive.infolist():
            mode = info.external_attr >> 16
            if mode and not info.is_dir():
                (package / info.filename).chmod(stat.S_IMODE(mode))
    shutil.copy2(ROOT / ".cache/corpus/subsets/lagoon-red.tif", fixture / "raster.tif")
    database = fixture / "terracotta.sqlite"
    subprocess.run(
        [
            str(bundle / "bin/rcfp"), "compat-copy",
            "--source", str(ROOT / "validation/artifacts/capture-reproduction/reference.sqlite"),
            "--output", str(database), "--key", "lagoon", "--key", "red",
        ],
        cwd=ROOT,
        check=True,
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE datasets SET path = '/data/raster.tif'")
    event = {
        "version": "2.0",
        "rawPath": "/singleband/lagoon/red/preview.png",
        "rawQueryString": "stretch_range=%5B0%2C3000%5D",
        "requestContext": {"http": {"method": "GET"}},
    }
    write_json(fixture / "event.json", event)
    invocation = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--entrypoint", "/opt/rcfp/bin/rcfp",
            "-v", f"{package / 'runtime'}:/opt/rcfp:ro",
            "-v", f"{fixture}:/data:ro",
            image,
            "lambda-event", "-d", "/data/terracotta.sqlite", "--event", "/data/event.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    response = json.loads(invocation.stdout)
    png = base64.b64decode(response["body"])
    (out / "lambda-al2023.png").write_bytes(png)
    golden = (ROOT / "validation/reference/capture/images/lagoon-red-preview.png").read_bytes()
    exact = bool(np.array_equal(decoded(png), decoded(golden)))
    binary = package / "runtime/bin/rcfp"
    versions = subprocess.run(
        ["objdump", "-T", str(binary)], check=True, text=True, capture_output=True
    ).stdout
    glibc_versions = sorted(
        {part.split(")")[0] for part in versions.split("GLIBC_")[1:]},
        key=lambda value: tuple(int(item) for item in value.split(".") if item.isdigit()),
    )
    report = {
        "passed": response["statusCode"] == 200 and response["isBase64Encoded"] and exact,
        "runtime_image": image,
        "runtime_family": "provided.al2023",
        "architecture": "x86_64",
        "maximum_required_glibc": glibc_versions[-1],
        "response_status": response["statusCode"],
        "decoded_rgba_exact": exact,
        "bundled_base_libraries": sorted(
            path.name
            for path in (package / "runtime/lib").iterdir()
            if path.name.startswith(("libexpat.so", "libgcc_s.so", "libstdc++.so", "libz.so"))
        ),
        "claim": "The Lambda zip's x86_64 binary and native GDAL dependency set load and render the pinned real raster in the official provided.al2023 runtime image.",
        "not_claimed": "This local runtime-image smoke is not an AWS Lambda service invocation.",
    }
    write_json(out / "report.json", report)
    shutil.rmtree(package)
    shutil.rmtree(fixture)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lambda-zip", type=Path, default=ROOT / "validation/artifacts/p03-lambda/rcfp-lambda.zip")
    parser.add_argument("--bundle", type=Path, default=ROOT / "validation/artifacts/p03-bundle")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()
    run(args.lambda_zip.resolve(), args.bundle.resolve(), args.out.resolve(), args.image)
