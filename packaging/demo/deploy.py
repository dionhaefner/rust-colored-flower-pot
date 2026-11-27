"""Deploy the demo through AWS CLI credentials, then check the public endpoint."""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--origin",
        required=True,
        action="append",
        help="Viewer origin without a path; repeat for multiple origins",
    )
    parser.add_argument("--stack", default="rcfp-demo")
    parser.add_argument("--budget-email", default="")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for value in args.origin:
        origin = urlsplit(value)
        if (
            not (
                origin.scheme == "https"
                or (
                    origin.scheme == "http"
                    and origin.hostname in ("localhost", "127.0.0.1", "::1")
                )
            )
            or not origin.hostname
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
        ):
            parser.error("origin must be HTTPS or loopback HTTP without a path")
    origins = [value.rstrip("/") for value in args.origin]
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,49}", args.stack):
        parser.error(
            "stack must start with a letter and contain at most 50 letters, digits or hyphens"
        )
    if not 1 <= args.concurrency <= 32:
        parser.error("concurrency must be between 1 and 32")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)

    def aws(*arguments):
        return subprocess.check_output(
            ["aws", "--region", args.region, "--no-cli-pager", *arguments], text=True
        )

    def outputs(stack):
        result = json.loads(
            aws("cloudformation", "describe-stacks", "--stack-name", stack)
        )
        return {
            item["OutputKey"]: item["OutputValue"]
            for item in result["Stacks"][0]["Outputs"]
        }

    storage = args.stack + "-storage"
    aws(
        "cloudformation",
        "deploy",
        "--template-file",
        str(HERE / "storage.yaml"),
        "--stack-name",
        storage,
        "--no-fail-on-empty-changeset",
    )
    resources = outputs(storage)
    if args.budget_email:
        account = json.loads(aws("sts", "get-caller-identity"))["Account"]
        budget = runpy.run_path(str(HERE / "budget.py"))["configure"](
            account, args.stack + "-account", args.budget_email
        )
        (out / "budget.json").write_text(json.dumps(budget, indent=2) + "\n")
    bucket, repository = resources["DataBucket"], resources["RepositoryUri"]
    subprocess.run(
        ["bash", str(HERE / "build.sh")],
        check=True,
        env={**os.environ, "DEMO_BUCKET": bucket, "DEMO_IMAGE": "rcfp-demo"},
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests/demo_contracts.py"),
            "--image",
            "rcfp-demo",
            "--out",
            str(out / "image-check"),
            "--remote-catalog",
        ],
        check=True,
    )
    container = subprocess.check_output(
        ["docker", "create", "rcfp-demo"], text=True
    ).strip()
    assets = out / "assets"
    assets.mkdir()
    try:
        for name in ("manifest.json", "data"):
            subprocess.run(
                ["docker", "cp", f"{container}:/var/task/{name}", str(assets)],
                check=True,
            )
    finally:
        subprocess.run(["docker", "rm", container], check=True)
    for row in json.loads((assets / "manifest.json").read_text())["fixtures"]:
        aws(
            "s3",
            "cp",
            str(assets / "data" / row["file"]),
            f"s3://{bucket}/{row['object_key']}",
            "--only-show-errors",
        )
    image_id = (
        subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", "rcfp-demo"],
            text=True,
        )
        .strip()
        .removeprefix("sha256:")
    )
    image_tag = f"{repository}:{image_id}"
    password = aws("ecr", "get-login-password")
    subprocess.run(
        [
            "docker",
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            repository.split("/")[0],
        ],
        input=password,
        text=True,
        check=True,
    )
    subprocess.run(["docker", "tag", "rcfp-demo", image_tag], check=True)
    images = json.loads(
        aws("ecr", "list-images", "--repository-name", resources["RepositoryName"])
    )["imageIds"]
    if not any(item.get("imageTag") == image_id for item in images):
        subprocess.run(["docker", "push", image_tag], check=True)
    image = json.loads(
        aws(
            "ecr",
            "describe-images",
            "--repository-name",
            resources["RepositoryName"],
            "--image-ids",
            f"imageTag={image_id}",
        )
    )["imageDetails"][0]
    parameters = {
        "ImageUri": repository + "@" + image["imageDigest"],
        "DataBucket": bucket,
        "AllowedOrigins": json.dumps([re.escape(origin) + "$" for origin in origins]),
        "Concurrency": str(args.concurrency),
    }
    (out / "parameters.json").write_text(json.dumps(parameters, indent=2) + "\n")

    def deploy_runtime():
        aws(
            "cloudformation",
            "deploy",
            "--template-file",
            str(HERE / "template.yaml"),
            "--stack-name",
            args.stack,
            "--capabilities",
            "CAPABILITY_IAM",
            "--parameter-overrides",
            *[f"{key}={value}" for key, value in parameters.items()],
            "--no-fail-on-empty-changeset",
        )

    deploy_runtime()
    deployed = outputs(args.stack)
    base = deployed["ApiUrl"].rstrip("/")
    parameters["PublicUrl"] = base
    (out / "parameters.json").write_text(json.dumps(parameters, indent=2) + "\n")
    deploy_runtime()
    for path in (
        "/keys",
        "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D",
        "/singleband/dolomites/elevation/preview.png",
        "/singleband/cape-town/elevation/preview.png",
    ):
        request = urllib.request.Request(base + path, headers={"Origin": origins[0]})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            if response.headers.get("Access-Control-Allow-Origin") != origins[0]:
                raise RuntimeError(
                    "public endpoint did not return the configured CORS origin"
                )
            if path.startswith("/singleband") and not body.startswith(
                b"\x89PNG\r\n\x1a\n"
            ):
                raise RuntimeError("public endpoint did not decode the PNG response")
    with urllib.request.urlopen(base + "/tilesets", timeout=60) as response:
        tilejson_path = json.load(response)["tilesets"][0]["tilejson"]
    with urllib.request.urlopen(base + tilejson_path, timeout=60) as response:
        tiles = json.load(response)["tiles"]
        if not tiles or any(not url.startswith(base + "/") for url in tiles):
            raise RuntimeError("TileJSON did not publish the configured HTTPS origin")
    report = {**deployed, **resources, "parameters": parameters}
    (out / "deployment.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(deployed, indent=2))


if __name__ == "__main__":
    main()
