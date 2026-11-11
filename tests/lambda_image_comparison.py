"""Verify both comparison images against independent pixels through Lambda's emulator."""

import argparse
import base64
import hashlib
import io
import json
import socket
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def event(path, method="GET"):
    url = urlsplit(path)
    return {
        "version": "2.0",
        "rawPath": url.path,
        "rawQueryString": url.query,
        "headers": {"host": "localhost", "origin": "http://127.0.0.1:8000"},
        "requestContext": {
            "stage": "$default",
            "http": {
                "method": method,
                "path": url.path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "comparison-check",
            },
        },
        "isBase64Encoded": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--images", nargs=2, required=True, metavar=("RCFP", "TERRACOTTA")
    )
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    prepared = args.prepared.resolve()
    manifest = json.loads((prepared / "manifest.json").read_text())
    expected = Image.open(ROOT / "tests/fixtures/demo/preview.png").convert("RGBA")
    preview_manifest = json.loads(
        (ROOT / "tests/fixtures/demo/manifest.json").read_text()
    )
    assert (
        hashlib.sha256(
            (ROOT / "tests/fixtures/demo/preview.png").read_bytes()
        ).hexdigest()
        == preview_manifest["preview_sha256"]
    )
    report = {"passed": False, "images": [], "aws_service_invocation": False}
    try:
        for image in args.images:
            identity = subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True
            ).strip()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            container = subprocess.check_output(
                [
                    "docker",
                    "run",
                    "-d",
                    "--memory",
                    "2g",
                    "-p",
                    f"127.0.0.1:{port}:8080",
                    "-v",
                    f"{prepared}:/reference/catalog:ro",
                    "-v",
                    f"{ROOT / 'docs/examples/data'}:/reference/demo:ro",
                    "-v",
                    f"{ROOT / '.cache/corpus'}:/reference/full:ro",
                    "-e",
                    "TC_DRIVER_PATH=/reference/catalog/catalog-local.sqlite",
                    "-e",
                    "TC_USE_MULTIPROCESSING=false",
                    "-e",
                    "TC_RASTER_WORKERS=1",
                    "-e",
                    "TC_RASTER_CACHE_SIZE=67108864",
                    "-e",
                    "TC_RASTER_CACHE_BYTES=67108864",
                    "-e",
                    "GDAL_CACHEMAX=64",
                    "-e",
                    'TC_ALLOWED_ORIGINS_METADATA=["http://127.0.0.1:8000"]',
                    "-e",
                    'TC_ALLOWED_ORIGINS_TILES=["http://127.0.0.1:8000"]',
                    image,
                ],
                text=True,
            ).strip()
            try:
                copied = args.out / (
                    "catalog-" + str(len(report["images"])) + ".sqlite"
                )
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        container + ":/var/task/catalog.sqlite",
                        str(copied),
                    ],
                    check=True,
                )
                assert (
                    hashlib.sha256(copied.read_bytes()).hexdigest()
                    == manifest["catalog_sha256"]
                )
                with sqlite3.connect(copied) as connection:
                    assert all(
                        row[0].startswith("s3://" + manifest["bucket"] + "/rasters/")
                        for row in connection.execute("SELECT path FROM datasets")
                    )

                def invoke(path, method="GET", port=port):
                    request = Request(
                        f"http://127.0.0.1:{port}/2015-03-31/functions/function/invocations",
                        data=json.dumps(event(path, method)).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urlopen(request, timeout=60) as response:
                        return json.load(response)

                for _ in range(100):
                    try:
                        result = invoke("/keys")
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise TimeoutError("runtime emulator did not start")
                assert result["statusCode"] == 200, result
                for _ in range(2):
                    result = invoke(
                        "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D"
                    )
                    assert result["statusCode"] == 200 and result["isBase64Encoded"], (
                        result
                    )
                    actual = Image.open(
                        io.BytesIO(base64.b64decode(result["body"]))
                    ).convert("RGBA")
                    assert (
                        actual.size == expected.size
                        and actual.tobytes() == expected.tobytes()
                    )
                for path in (
                    "/singleband/sentinel/red/13/4377/2931.png?stretch_range=%5B0%2C3000%5D",
                    "/rgb/sentinel/13/4377/2931.png?r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D",
                ):
                    result = invoke(path)
                    assert result["statusCode"] == 200 and result["isBase64Encoded"], (
                        result
                    )
                    assert Image.open(
                        io.BytesIO(base64.b64decode(result["body"]))
                    ).size == (256, 256)
                assert (
                    invoke("/singleband/missing/red/preview.png")["statusCode"] == 404
                )
                assert invoke("/keys", "OPTIONS")["statusCode"] == 200
                report["images"].append(
                    {
                        "image": image,
                        "id": identity,
                        "catalog_sha256": manifest["catalog_sha256"],
                        "passed": True,
                    }
                )
            finally:
                logs = subprocess.check_output(
                    ["docker", "logs", container], stderr=subprocess.STDOUT, text=True
                )
                (args.out / (image.replace("/", "-") + ".log")).write_text(logs)
                subprocess.run(
                    ["docker", "rm", "-f", container],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
        report["passed"] = True
    finally:
        (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
