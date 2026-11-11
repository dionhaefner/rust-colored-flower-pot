"""Qualify and deploy separate Terracotta and RCFP Lambda comparison functions."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def aws(region, *arguments):
    output = subprocess.check_output(
        ["aws", "--region", region, "--no-cli-pager", "--output", "json", *arguments],
        text=True,
    )
    if arguments[:2] == ("cloudformation", "deploy"):
        return output
    return json.loads(output) if output.strip() else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--repository-uri", required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--prefix", default="rcfp-compare")
    parser.add_argument("--rcfp-base-image", default="rcfp-demo")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,37}", args.prefix):
        parser.error(
            "prefix must start with a letter and use at most 38 letters, digits or hyphens"
        )
    args.out.mkdir(parents=True, exist_ok=False)
    prepared = args.prepared.resolve()
    manifest = json.loads((prepared / "manifest.json").read_text())
    if (
        hashlib.sha256((prepared / "catalog.sqlite").read_bytes()).hexdigest()
        != manifest["catalog_sha256"]
    ):
        raise ValueError("catalog checksum mismatch")
    for row in manifest["sources"]:
        remote = aws(
            args.region,
            "s3api",
            "head-object",
            "--bucket",
            manifest["bucket"],
            "--key",
            row["key"],
        )
        if remote["ContentLength"] != row["bytes"]:
            raise ValueError("uploaded raster size differs from the pinned source")
    images = {
        "native": "rcfp-lambda-comparison",
        "terracotta": "terracotta-lambda-comparison",
    }
    for engine, image in images.items():
        if not args.skip_build:
            build = [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--provenance=false",
                "-f",
                str(
                    HERE
                    / (
                        "Dockerfile.rcfp"
                        if engine == "native"
                        else "Dockerfile.terracotta"
                    )
                ),
                "-t",
                image,
            ]
            if engine == "native":
                build += ["--build-arg", "RCFP_IMAGE=" + args.rcfp_base_image]
            subprocess.run([*build, str(prepared)], check=True)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests/lambda_image_comparison.py"),
            "--images",
            *images.values(),
            "--prepared",
            str(prepared),
            "--out",
            str(args.out / "image-check"),
        ],
        check=True,
    )
    password = subprocess.check_output(
        ["aws", "--region", args.region, "ecr", "get-login-password"], text=True
    )
    subprocess.run(
        [
            "docker",
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            args.repository_uri.split("/")[0],
        ],
        input=password,
        text=True,
        check=True,
    )
    report = {"region": args.region, "manifest": manifest, "functions": {}}
    try:
        for engine, image in images.items():
            identity = (
                subprocess.check_output(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", image],
                    text=True,
                )
                .strip()
                .removeprefix("sha256:")
            )
            tag = engine + "-" + identity
            destination = args.repository_uri + ":" + tag
            subprocess.run(["docker", "tag", image, destination], check=True)
            subprocess.run(["docker", "push", destination], check=True)
            record = aws(
                args.region,
                "ecr",
                "describe-images",
                "--repository-name",
                args.repository_uri.split("/", 1)[1],
                "--image-ids",
                "imageTag=" + tag,
            )["imageDetails"][0]
            parameters = {
                "ImageUri": args.repository_uri + "@" + record["imageDigest"],
                "DataBucket": manifest["bucket"],
                "AllowedOrigins": json.dumps(
                    [r"http://127\.0\.0\.1:8000$", r"http://localhost:8000$"]
                ),
                "Concurrency": "8",
                "Profile": "native",
            }
            name = args.prefix + "-" + engine

            def deploy(name=name, parameters=parameters):
                aws(
                    args.region,
                    "cloudformation",
                    "deploy",
                    "--template-file",
                    str(HERE / "template.yaml"),
                    "--stack-name",
                    name,
                    "--capabilities",
                    "CAPABILITY_IAM",
                    "--parameter-overrides",
                    *[f"{key}={value}" for key, value in parameters.items()],
                    "--no-fail-on-empty-changeset",
                )

            deploy()
            stack = aws(
                args.region, "cloudformation", "describe-stacks", "--stack-name", name
            )["Stacks"][0]
            outputs = {row["OutputKey"]: row["OutputValue"] for row in stack["Outputs"]}
            parameters["PublicUrl"] = outputs["ApiUrl"].rstrip("/")
            deploy()
            configuration = aws(
                args.region,
                "lambda",
                "get-function-configuration",
                "--function-name",
                name,
            )
            report["functions"][engine] = {
                **outputs,
                "stack_id": stack["StackId"],
                "parameters": parameters,
                "configuration": configuration,
            }
            print(engine, outputs["ApiUrl"], flush=True)
    finally:
        (args.out / "deployment.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
