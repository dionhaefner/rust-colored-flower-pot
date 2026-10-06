"""Remove the demo function, private raster copies and deployment images."""

import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def aws(*arguments):
        return subprocess.check_output(
            ["aws", "--region", args.region, "--no-cli-pager", *arguments], text=True
        )

    storage = args.stack + "-storage"
    stack = json.loads(
        aws("cloudformation", "describe-stacks", "--stack-name", storage)
    )["Stacks"][0]
    resources = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    (args.out / "removed-storage.json").write_text(
        json.dumps(resources, indent=2) + "\n"
    )
    stacks = json.loads(aws("cloudformation", "list-stacks"))["StackSummaries"]
    if any(
        item["StackName"] == args.stack and item["StackStatus"] != "DELETE_COMPLETE"
        for item in stacks
    ):
        aws(
            "lambda",
            "put-function-concurrency",
            "--function-name",
            args.stack,
            "--reserved-concurrent-executions",
            "0",
        )
        aws("cloudformation", "delete-stack", "--stack-name", args.stack)
        aws(
            "cloudformation",
            "wait",
            "stack-delete-complete",
            "--stack-name",
            args.stack,
        )
    aws(
        "s3",
        "rm",
        f"s3://{resources['DataBucket']}",
        "--recursive",
        "--only-show-errors",
    )
    images = json.loads(
        aws("ecr", "list-images", "--repository-name", resources["RepositoryName"])
    )["imageIds"]
    for start in range(0, len(images), 100):
        result = json.loads(
            aws(
                "ecr",
                "batch-delete-image",
                "--repository-name",
                resources["RepositoryName"],
                "--image-ids",
                json.dumps(images[start : start + 100]),
            )
        )
        if result.get("failures"):
            raise RuntimeError(f"ECR images could not be removed: {result['failures']}")
    aws("cloudformation", "delete-stack", "--stack-name", storage)
    aws("cloudformation", "wait", "stack-delete-complete", "--stack-name", storage)


if __name__ == "__main__":
    main()
