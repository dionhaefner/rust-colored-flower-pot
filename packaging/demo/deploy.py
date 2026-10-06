"""Deploy the demo through AWS CLI credentials, then check the public endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
        "--origin", required=True, help="Pages origin, without project path"
    )
    parser.add_argument("--stack", default="rcfp-demo")
    parser.add_argument("--budget-email", default="")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    origin = urlsplit(args.origin)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
        or origin.username
        or origin.password
    ):
        parser.error("origin must be an HTTPS origin without a project path")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,49}", args.stack):
        parser.error(
            "stack must start with a letter and contain at most 50 letters, digits or hyphens"
        )
    if not 1 <= args.concurrency <= 32:
        parser.error("concurrency must be between 1 and 32")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rows = json.loads((ROOT / "docs/examples/data/manifest.json").read_text())[
        "fixtures"
    ]
    rows = [row for row in rows if row["id"] in ("red", "green", "blue", "nir")]
    for row in rows:
        source = ROOT / "docs/examples/data" / row["file"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"sample checksum mismatch: {source}")

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
    bucket, repository = resources["DataBucket"], resources["RepositoryUri"]
    for row in rows:
        key = f"rasters/{row['sha256']}/{row['file']}"
        aws(
            "s3",
            "cp",
            str(ROOT / "docs/examples/data" / row["file"]),
            f"s3://{bucket}/{key}",
            "--only-show-errors",
        )
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
        "AllowedOrigins": json.dumps([re.escape(args.origin.rstrip("/")) + "$"]),
        "Concurrency": str(args.concurrency),
        "BudgetEmail": args.budget_email,
    }
    overrides = [f"{key}={value}" for key, value in parameters.items()]
    (out / "parameters.json").write_text(json.dumps(parameters, indent=2) + "\n")
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
        *overrides,
        "--no-fail-on-empty-changeset",
    )
    deployed = outputs(args.stack)
    base = deployed["ApiUrl"].rstrip("/")
    for path in (
        "/keys",
        "/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D",
    ):
        request = urllib.request.Request(
            base + path, headers={"Origin": args.origin.rstrip("/")}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            if response.headers.get(
                "Access-Control-Allow-Origin"
            ) != args.origin.rstrip("/"):
                raise RuntimeError(
                    "public endpoint did not return the configured CORS origin"
                )
            if path.startswith("/singleband") and not body.startswith(
                b"\x89PNG\r\n\x1a\n"
            ):
                raise RuntimeError("public endpoint did not decode the PNG response")
    report = {**deployed, **resources, "parameters": parameters}
    (out / "deployment.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(deployed, indent=2))


if __name__ == "__main__":
    main()
