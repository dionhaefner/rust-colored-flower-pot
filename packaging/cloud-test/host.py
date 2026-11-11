"""Provision, deploy to, collect and remove a disposable EC2 test host."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import re
import runpy
import shlex
import sqlite3
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AMI_PARAMETER = (
    "/aws/service/canonical/ubuntu/server/noble/stable/current/amd64/hvm/ebs-gp3/ami-id"
)
INSTANCE_TYPES = (
    "c7i.large",
    "c7i.xlarge",
    "c7i.2xlarge",
    "c7i.4xlarge",
    "c7i-flex.large",
    "m7i-flex.large",
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class AWS:
    def __init__(self, region):
        self.region = region

    def call(self, *arguments):
        return subprocess.check_output(
            [
                "aws",
                "--region",
                self.region,
                "--no-cli-pager",
                "--output",
                "json",
                *arguments,
            ],
            text=True,
        )

    def json(self, *arguments):
        return json.loads(self.call(*arguments))

    def outputs(self, stack):
        record = self.json("cloudformation", "describe-stacks", "--stack-name", stack)
        return {
            row["OutputKey"]: row["OutputValue"]
            for row in record["Stacks"][0]["Outputs"]
        }

    def command(self, instance, command, log=None, timeout=900):
        request = self.json(
            "ssm",
            "send-command",
            "--instance-ids",
            instance,
            "--document-name",
            "AWS-RunShellScript",
            "--timeout-seconds",
            "60",
            "--parameters",
            json.dumps({"commands": [command], "executionTimeout": [str(timeout)]}),
        )
        command_id = request["Command"]["CommandId"]
        deadline = time.monotonic() + timeout + 90
        while time.monotonic() < deadline:
            try:
                result = self.json(
                    "ssm",
                    "get-command-invocation",
                    "--command-id",
                    command_id,
                    "--instance-id",
                    instance,
                )
            except subprocess.CalledProcessError:
                time.sleep(2)
                continue
            if result["Status"] in ("Pending", "InProgress", "Delayed", "Cancelling"):
                time.sleep(2)
                continue
            if log:
                save(log, result)
            if result["Status"] != "Success":
                raise RuntimeError(
                    f"SSM command {command_id}: {result['Status']}; collect its output"
                )
            return result["StandardOutputContent"]
        raise TimeoutError(f"SSM command {command_id} did not complete")


def hourly_rate(region, instance_type):
    filters = {
        "regionCode": region,
        "instanceType": instance_type,
        "operatingSystem": "Linux",
        "tenancy": "Shared",
        "preInstalledSw": "NA",
        "capacitystatus": "Used",
    }
    result = AWS("us-east-1").json(
        "pricing",
        "get-products",
        "--service-code",
        "AmazonEC2",
        "--filters",
        json.dumps(
            [
                {"Type": "TERM_MATCH", "Field": key, "Value": value}
                for key, value in filters.items()
            ]
        ),
    )
    prices = {
        float(dimension["pricePerUnit"]["USD"])
        for product in result["PriceList"]
        for term in json.loads(product)["terms"]["OnDemand"].values()
        for dimension in term["priceDimensions"].values()
        if dimension["unit"] == "Hrs" and dimension["beginRange"] == "0"
    }
    if len(prices) != 1 or next(iter(prices)) <= 0:
        raise ValueError("could not identify one on-demand Linux hourly rate")
    return prices.pop()


def up(args):
    client = AWS(args.region)
    price = args.hourly_usd or hourly_rate(args.region, args.instance_type)
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(hours=args.ttl_hours)
    image_id = (
        args.ami_id
        or client.json("ssm", "get-parameter", "--name", AMI_PARAMETER)["Parameter"][
            "Value"
        ]
    )
    image = client.json("ec2", "describe-images", "--image-ids", image_id)["Images"][0]
    if (
        image["OwnerId"] != "099720109477"
        or image["Architecture"] != "x86_64"
        or "ubuntu-noble-24.04-amd64-server" not in image["Name"]
    ):
        raise ValueError("expected an official Canonical x86_64 Ubuntu 24.04 image")
    plan = {
        "region": args.region,
        "stack": args.stack,
        "storage_stack": None
        if getattr(args, "data_bucket", None)
        else args.stack + "-storage",
        "instance_type": args.instance_type,
        "ami_id": image_id,
        "ami_name": image["Name"],
        "client_cidr": args.client_cidr,
        "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "compute_hourly_usd": price,
        "compute_session_estimate_usd": round(price * args.ttl_hours, 4),
        "price_source": "operator" if args.hourly_usd else "AWS Price List API",
        "estimate_excludes": ["EBS", "public IPv4", "S3", "data transfer", "tax"],
    }
    save(args.out / "plan.json", plan)
    print(json.dumps(plan, indent=2), flush=True)
    if args.dry_run:
        return
    bucket = getattr(args, "data_bucket", None)
    if bucket is None:
        client.call(
            "cloudformation",
            "deploy",
            "--template-file",
            str(HERE / "storage.yaml"),
            "--stack-name",
            plan["storage_stack"],
            "--no-fail-on-empty-changeset",
        )
        bucket = client.outputs(plan["storage_stack"])["DataBucket"]
    parameters = {
        "ImageId": image_id,
        "RootDevice": image["RootDeviceName"],
        "InstanceType": args.instance_type,
        "ClientCidr": args.client_cidr,
        "DataBucket": bucket,
        "ExpiresAt": plan["expires_at"],
    }
    if getattr(args, "image_repository_arn", None):
        parameters["ImageRepositoryArn"] = args.image_repository_arn
    if getattr(args, "command_target_arn", None):
        parameters["CommandTargetInstanceArn"] = args.command_target_arn
        parameters["CommandReportsPrefix"] = args.command_reports_prefix
    # Create a new stack so an expired host cannot silently reuse stale state.
    created = client.json(
        "cloudformation",
        "create-stack",
        "--stack-name",
        args.stack,
        "--template-body",
        "file://" + str(HERE / "template.yaml"),
        "--capabilities",
        "CAPABILITY_IAM",
        "--parameters",
        json.dumps(
            [
                {"ParameterKey": key, "ParameterValue": value}
                for key, value in parameters.items()
            ]
        ),
    )
    stack_id = created["StackId"]
    try:
        client.call(
            "cloudformation",
            "wait",
            "stack-create-complete",
            "--stack-name",
            stack_id,
        )
        resources = client.outputs(stack_id)
        session = {
            **plan,
            **resources,
            "bucket": bucket,
            "session_id": uuid.uuid4().hex,
            "stack_id": stack_id,
        }
        save(args.out / "session.json", session)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            managed = client.json(
                "ssm",
                "describe-instance-information",
                "--filters",
                f"Key=InstanceIds,Values={resources['InstanceId']}",
            )["InstanceInformationList"]
            if any(row["PingStatus"] == "Online" for row in managed):
                break
            time.sleep(5)
        else:
            raise TimeoutError("host did not register with Systems Manager")
        client.command(
            resources["InstanceId"],
            "cloud-init status --wait; test -e /var/lib/rcfp-ready",
            args.out / "bootstrap.json",
        )
    except BaseException:
        client.call("cloudformation", "delete-stack", "--stack-name", stack_id)
        client.call(
            "cloudformation",
            "wait",
            "stack-delete-complete",
            "--stack-name",
            stack_id,
        )
        raise
    print(f"Ready: {resources['InstanceId']}; session: {args.out / 'session.json'}")


def package(bundle, catalog, bucket, out):
    binary = bundle / "bin/rcfp"
    with binary.open("rb") as stream:
        header = stream.read(20)
    if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
        raise ValueError("the VM requires an x86_64 Linux native bundle")
    for name in ("lib", "share", "licenses"):
        if not (bundle / name).is_dir():
            raise ValueError(f"native bundle is missing {name}")
    uploads = []
    database = out / "catalog.sqlite"
    if catalog is None:
        prepare = runpy.run_path(str(ROOT / "packaging/demo/prepare.py"))["prepare"]
        demo = out / "sample"
        manifest = prepare(binary, demo, bucket)
        catalog = demo / "catalog.sqlite"
        uploads = [
            (demo / "data" / row["file"], row["object_key"])
            for row in manifest["fixtures"]
        ]
        fixtures = manifest["fixtures"]
    else:
        fixtures = []
    with (
        sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as source,
        sqlite3.connect(database) as target,
    ):
        source.backup(target)
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "terracotta" not in tables or "metadata" not in tables:
            raise ValueError(
                "use a precomputed Terracotta-format SQLite catalog with local TIFF paths"
            )
        if connection.execute("SELECT COUNT(*) FROM metadata").fetchone()[
            0
        ] != connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] or any(
            row[0] is None
            for row in connection.execute("SELECT metadata FROM metadata")
        ):
            raise ValueError("compute all raster metadata before deployment")
        paths = [
            row[0] for row in connection.execute("SELECT DISTINCT path FROM datasets")
        ]
        if not paths:
            raise ValueError("catalog contains no datasets")
        for path in paths:
            if catalog.parent == out / "sample":
                continue
            source = Path(path)
            if not source.is_absolute() or source.suffix.lower() not in (
                ".tif",
                ".tiff",
            ):
                raise ValueError(
                    "catalog raster paths must be absolute local TIFF paths"
                )
            if any(
                Path(str(source) + suffix).exists()
                for suffix in (".msk", ".ovr", ".aux.xml")
            ):
                raise ValueError(
                    "use self-contained TIFFs without external mask, overview or auxiliary files"
                )
            checksum = digest(source)
            key = f"rasters/{checksum}/{source.name}"
            uploads.append((source, key))
            fixtures.append(
                {"file": source.name, "sha256": checksum, "object_key": key}
            )
            connection.execute(
                "UPDATE datasets SET path=? WHERE path=?",
                (f"s3://{bucket}/{key}", path),
            )
    manifest = {
        "binary_sha256": digest(binary),
        "catalog_sha256": digest(database),
        "fixtures": fixtures,
    }
    save(out / "manifest.json", manifest)
    archive = out / "deployment.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=True, compresslevel=1) as packed:
        for name in ("bin", "lib", "share", "licenses"):
            packed.add(bundle / name, arcname="runtime/" + name)
        packed.add(database, arcname="catalog.sqlite")
        packed.add(out / "manifest.json", arcname="manifest.json")
    return archive, manifest, uploads


def deploy(args, session, client):
    previous_session = session
    archive, manifest, uploads = package(
        args.bundle.resolve(),
        args.catalog.resolve() if args.catalog else None,
        session["bucket"],
        args.out,
    )
    for source, key in uploads:
        if digest(source) != key.split("/")[1]:
            raise ValueError(
                f"raster changed while preparing deployment: {source.name}"
            )
        client.call(
            "s3",
            "cp",
            str(source),
            f"s3://{session['bucket']}/{key}",
            "--only-show-errors",
        )
    checksum = digest(archive)
    key = f"artifacts/{checksum}/deployment.tar.gz"
    client.call(
        "s3",
        "cp",
        str(archive),
        f"s3://{session['bucket']}/{key}",
        "--only-show-errors",
    )
    session = {
        **session,
        "report_prefix": f"runs/{session['stack']}/{session['session_id']}/{uuid.uuid4().hex}/",
        "deployment": {
            **manifest,
            "archive_sha256": checksum,
            "workers": args.workers,
            "profile": args.profile,
        },
    }
    save(args.out / "deployment-session.json", session)
    if "report_prefix" not in previous_session:
        save(args.session, session)
    script = base64.b64encode((HERE / "remote.py").read_bytes()).decode()
    command = (
        f"printf %s {shlex.quote(script)} | base64 -d > /opt/rcfp/remote.py\n"
        + shlex.join(
            [
                "/usr/bin/python3",
                "/opt/rcfp/remote.py",
                "deploy",
                "--bucket",
                session["bucket"],
                "--key",
                key,
                "--sha256",
                checksum,
                "--prefix",
                session["report_prefix"],
                "--region",
                session["region"],
                "--workers",
                str(args.workers),
                "--profile",
                args.profile,
            ]
        )
    )
    client.command(session["InstanceId"], command, args.out / "deploy-command.json")
    save(args.session, session)
    print(session["ApiUrl"])


def collect(args, session, client, stop=False):
    if "report_prefix" not in session:
        raise ValueError("no deployment has been recorded in this session")
    instances = client.json(
        "ec2",
        "describe-instances",
        "--filters",
        f"Name=instance-id,Values={session['InstanceId']}",
    )
    states = [
        item["State"]["Name"]
        for row in instances["Reservations"]
        for item in row["Instances"]
    ]
    state = states[0] if states else "terminated"
    if state == "running":
        collected = json.loads(
            client.command(
                session["InstanceId"],
                "/usr/bin/python3 /opt/rcfp/remote.py collect"
                + (" --stop" if stop else ""),
                args.out / "collect-command.json",
                timeout=120,
            )
        )
        if collected["bucket"] != session["bucket"] or not collected[
            "prefix"
        ].startswith(f"runs/{session['stack']}/{session['session_id']}/"):
            raise ValueError(
                "host returned a report location outside its recorded session"
            )
        session["report_prefix"] = collected["prefix"]
    client.call(
        "s3",
        "cp",
        f"s3://{session['bucket']}/{session['report_prefix']}",
        str(args.out / "server"),
        "--recursive",
        "--only-show-errors",
    )
    if not (args.out / "server/host.json").exists():
        raise RuntimeError(
            "no host report was available in S3; collection did not pass"
        )
    session["deployment"] = json.loads(
        (args.out / "server/deployment.json").read_text()
    )
    save(args.session, session)
    save(args.out / "session.json", session)


def down(args, session, client):
    collection_error = None
    if "report_prefix" in session:
        try:
            collect(args, session, client, stop=True)
        except Exception as exc:  # noqa: BLE001 -- collection must not prevent teardown
            collection_error = str(exc)
    client.call("cloudformation", "delete-stack", "--stack-name", session["stack_id"])
    client.call(
        "cloudformation",
        "wait",
        "stack-delete-complete",
        "--stack-name",
        session["stack_id"],
    )
    save(
        args.out / "teardown.json",
        {
            "host_removed": True,
            "collection_error": collection_error,
            "storage_retained": session["storage_stack"] or session["bucket"],
        },
    )
    if collection_error:
        raise RuntimeError(
            f"host removed, but result collection failed: {collection_error}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("up")
    create.add_argument("--region", required=True)
    create.add_argument("--stack", default="rcfp-cloud-test")
    create.add_argument("--client-cidr", required=True)
    create.add_argument(
        "--instance-type", choices=INSTANCE_TYPES, default="c7i.2xlarge"
    )
    create.add_argument("--ttl-hours", type=float, default=4)
    create.add_argument("--ami-id")
    create.add_argument(
        "--data-bucket", help="reuse an existing bucket instead of creating storage"
    )
    create.add_argument(
        "--image-repository-arn", help="allow read-only pulls from one ECR repository"
    )
    create.add_argument(
        "--command-target-arn",
        help="single benchmark EC2 instance this host may control through SSM",
    )
    create.add_argument(
        "--command-reports-prefix", help="server report prefix this host may read"
    )
    create.add_argument(
        "--hourly-usd",
        type=float,
        help="override the compute estimate if pricing:GetProducts is unavailable",
    )
    create.add_argument("--dry-run", action="store_true")
    for name in ("deploy", "collect", "down", "shell", "exec", "purge-storage"):
        action = commands.add_parser(name)
        action.add_argument("--session", type=Path, required=True)
        if name == "deploy":
            action.add_argument("--bundle", type=Path, required=True)
            action.add_argument("--catalog", type=Path)
            action.add_argument("--workers", type=int, default=4)
            action.add_argument(
                "--profile", choices=("native", "terracotta"), default="native"
            )
        if name == "exec":
            action.add_argument("--command", dest="script", required=True)
    for name in ("up", "deploy", "collect", "down", "exec", "purge-storage"):
        commands.choices[name].add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "up" and bool(args.command_target_arn) != bool(
        args.command_reports_prefix
    ):
        parser.error(
            "--command-target-arn and --command-reports-prefix must be supplied together"
        )
    if args.command == "up":
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,49}", args.stack):
            parser.error(
                "stack must start with a letter and use at most 50 letters, digits or hyphens"
            )
        try:
            address = ipaddress.ip_network(args.client_cidr)
        except ValueError:
            parser.error("client CIDR must be one public IPv4 address with /32")
        if (
            address.version != 4
            or address.prefixlen != 32
            or not address.network_address.is_global
        ):
            parser.error("client CIDR must be one public IPv4 address with /32")
        if not 0.25 <= args.ttl_hours <= 24:
            parser.error("TTL must be between 0.25 and 24 hours")
        if args.hourly_usd is not None and not 0 < args.hourly_usd < 100:
            parser.error("hourly estimate must be positive and below USD 100")
    if args.command == "deploy" and not 1 <= args.workers <= 8:
        parser.error("workers must be between 1 and 8")
    if hasattr(args, "out"):
        args.out = args.out.resolve()
        args.out.mkdir(parents=True, exist_ok=False)
    if args.command == "up":
        up(args)
        return
    session = json.loads(args.session.read_text())
    client = AWS(session["region"])
    if args.command == "deploy":
        deploy(args, session, client)
    elif args.command == "collect":
        collect(args, session, client)
    elif args.command == "down":
        down(args, session, client)
    elif args.command == "shell":
        subprocess.run(
            [
                "aws",
                "--region",
                session["region"],
                "ssm",
                "start-session",
                "--target",
                session["InstanceId"],
            ],
            check=True,
        )
    elif args.command == "exec":
        print(
            client.command(
                session["InstanceId"], args.script, args.out / "command.json"
            )
        )
    else:
        if session["storage_stack"] is None:
            raise ValueError("shared storage is not owned by this test session")
        # Refuse to remove retained data while its host stack still exists.
        stacks = client.json("cloudformation", "list-stacks")["StackSummaries"]
        if any(
            row["StackName"] == session["stack"]
            and row["StackStatus"] != "DELETE_COMPLETE"
            for row in stacks
        ):
            raise ValueError("remove the host stack before purging its storage")
        bucket = client.outputs(session["storage_stack"])["DataBucket"]
        if bucket != session["bucket"]:
            raise ValueError("storage output differs from the session bucket")
        client.call("s3", "rm", f"s3://{bucket}", "--recursive", "--only-show-errors")
        client.call(
            "cloudformation", "delete-stack", "--stack-name", session["storage_stack"]
        )
        client.call(
            "cloudformation",
            "wait",
            "stack-delete-complete",
            "--stack-name",
            session["storage_stack"],
        )
        save(args.out / "purged.json", {"bucket": bucket, "storage_removed": True})


if __name__ == "__main__":
    main()
