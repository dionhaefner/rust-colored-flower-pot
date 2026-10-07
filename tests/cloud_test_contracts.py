"""Qualify disposable-host packaging and lifecycle without an AWS account."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import socket
import sqlite3
import subprocess
import tarfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]


def helper(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = helper(ROOT / "packaging/cloud-test/host.py")
probe = helper(ROOT / "tests/cloud_deployment.py")
remote = helper(ROOT / "packaging/cloud-test/remote.py")


class RecordingAWS:
    def __init__(self, bootstrap_error=False, create_error=False):
        self.calls = []
        self.bootstrap_error = bootstrap_error
        self.create_error = create_error

    def call(self, *arguments):
        self.calls.append(arguments)
        return ""

    def json(self, *arguments):
        self.calls.append(arguments)
        if arguments[:2] == ("ec2", "describe-images"):
            return {
                "Images": [
                    {
                        "OwnerId": "099720109477",
                        "Architecture": "x86_64",
                        "Name": "ubuntu-noble-24.04-amd64-server-test",
                        "RootDeviceName": "/dev/sda1",
                    }
                ]
            }
        if arguments[:2] == ("cloudformation", "create-stack"):
            if self.create_error:
                raise RuntimeError("stack already exists")
            return {
                "StackId": "arn:aws:cloudformation:region:account:stack/test/unique"
            }
        if arguments[:2] == ("ssm", "describe-instance-information"):
            return {"InstanceInformationList": [{"PingStatus": "Online"}]}
        raise AssertionError(arguments)

    def outputs(self, stack):
        if stack.endswith("-storage"):
            return {"DataBucket": "test-bucket"}
        return {"InstanceId": "i-test", "ApiUrl": "http://example.invalid:5000"}

    def command(self, instance, command, *_args, **_kwargs):
        self.calls.append(("command", instance, command))
        if self.bootstrap_error:
            raise RuntimeError("bootstrap failed")
        return ""


class Contracts(unittest.TestCase):
    bundle: Path
    out: Path

    def folder(self):
        path = self.out / self.id().rsplit(".", 1)[1]
        path.mkdir()
        return path

    def arguments(self, out):
        return SimpleNamespace(
            region="eu-north-1",
            stack="test",
            hourly_usd=0.4,
            instance_type="c7i.2xlarge",
            ttl_hours=4,
            ami_id="ami-test",
            client_cidr="8.8.8.8/32",
            out=out,
            dry_run=False,
        )

    def test_plan_has_no_mutations(self):
        args = self.arguments(self.folder())
        args.dry_run = True
        client = RecordingAWS()
        with patch.object(host, "AWS", return_value=client):
            host.up(args)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][:2], ("ec2", "describe-images"))
        self.assertEqual(
            json.loads((args.out / "plan.json").read_text())[
                "compute_session_estimate_usd"
            ],
            1.6,
        )

    def test_exec_dispatches_operator_command(self):
        out = self.folder()
        session = out / "session.json"
        host.save(session, {"region": "eu-north-1", "InstanceId": "i-test"})
        client = RecordingAWS()
        with (
            patch.object(host, "AWS", return_value=client),
            patch(
                "sys.argv",
                [
                    "host.py",
                    "exec",
                    "--session",
                    str(session),
                    "--command",
                    "uname -a",
                    "--out",
                    str(out / "command"),
                ],
            ),
        ):
            host.main()
        self.assertEqual(client.calls, [("command", "i-test", "uname -a")])

    def test_bad_archive_preserves_existing_deployment(self):
        out = self.folder()
        current = out / "deployment"
        current.mkdir()
        (current / "marker").write_text("running deployment")
        args = SimpleNamespace(
            workers=4,
            region="eu-north-1",
            bucket="test",
            key="artifact",
            sha256="0" * 64,
        )

        def corrupt_download(_bucket, _key, destination):
            Path(destination).write_bytes(b"invalid downloaded archive")

        with (
            patch.object(remote, "BASE", out),
            patch.object(remote, "physical_cpus", return_value=[0, 2, 4, 6]),
            patch.object(remote, "s3") as client,
            patch.object(remote.subprocess, "run") as run,
        ):
            client.return_value.download_file.side_effect = corrupt_download
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                remote.install(args)
            run.assert_not_called()
        self.assertEqual((current / "marker").read_text(), "running deployment")

    def test_failed_ssm_command_retains_output(self):
        out = self.folder()
        client = host.AWS("eu-north-1")
        result = {
            "Status": "Failed",
            "StandardOutputContent": "",
            "StandardErrorContent": "service failed",
        }
        with (
            patch.object(
                client,
                "json",
                side_effect=[{"Command": {"CommandId": "test-command"}}, result],
            ),
            self.assertRaisesRegex(RuntimeError, "test-command: Failed"),
        ):
            client.command("i-test", "false", out / "command.json")
        self.assertEqual(json.loads((out / "command.json").read_text()), result)

    def test_bootstrap_failure_removes_only_new_host(self):
        args = self.arguments(self.folder())
        client = RecordingAWS(bootstrap_error=True)
        with (
            patch.object(host, "AWS", return_value=client),
            self.assertRaisesRegex(RuntimeError, "bootstrap failed"),
        ):
            host.up(args)
        deletes = [
            row for row in client.calls if row[:2] == ("cloudformation", "delete-stack")
        ]
        self.assertEqual(
            deletes,
            [
                (
                    "cloudformation",
                    "delete-stack",
                    "--stack-name",
                    "arn:aws:cloudformation:region:account:stack/test/unique",
                )
            ],
        )
        self.assertTrue((args.out / "session.json").exists())

    def test_existing_stack_is_not_deleted(self):
        args = self.arguments(self.folder())
        client = RecordingAWS(create_error=True)
        with (
            patch.object(host, "AWS", return_value=client),
            self.assertRaisesRegex(RuntimeError, "already exists"),
        ):
            host.up(args)
        self.assertFalse(
            any(row[:2] == ("cloudformation", "delete-stack") for row in client.calls)
        )

    def test_collection_failure_still_removes_host(self):
        args = SimpleNamespace(out=self.folder())
        session = {
            "stack": "test",
            "stack_id": "unique-stack-id",
            "report_prefix": "runs/test/",
            "storage_stack": "test-storage",
        }
        client = RecordingAWS()
        with (
            patch.object(host, "collect", side_effect=RuntimeError("S3 unavailable")),
            self.assertRaisesRegex(RuntimeError, "host removed"),
        ):
            host.down(args, session, client)
        self.assertIn(
            ("cloudformation", "delete-stack", "--stack-name", "unique-stack-id"),
            client.calls,
        )
        report = json.loads((args.out / "teardown.json").read_text())
        self.assertTrue(report["host_removed"])
        self.assertEqual(report["collection_error"], "S3 unavailable")

    def test_expired_host_reports_can_be_collected(self):
        out = self.folder()
        session = {
            "InstanceId": "i-expired",
            "bucket": "test-bucket",
            "region": "eu-north-1",
            "report_prefix": "runs/test/session/",
            "stack": "test",
            "session_id": "session",
        }
        args = SimpleNamespace(out=out, session=out / "session.json")
        host.save(args.session, session)
        client = RecordingAWS()

        def download(*arguments):
            client.calls.append(arguments)
            destination = Path(arguments[3])
            destination.mkdir()
            host.save(destination / "host.json", {"collected_at": 123})
            host.save(
                destination / "deployment.json", {"binary_sha256": "known-artifact"}
            )
            return ""

        with (
            patch.object(client, "json", return_value={"Reservations": []}),
            patch.object(client, "call", side_effect=download),
        ):
            host.collect(args, session, client)
        self.assertFalse(any(row[0] == "command" for row in client.calls))
        self.assertEqual(
            json.loads(args.session.read_text())["deployment"]["binary_sha256"],
            "known-artifact",
        )

    def test_missing_expiry_reports_are_not_a_pass(self):
        out = self.folder()
        args = SimpleNamespace(out=out, session=out / "session.json")
        session = {
            "InstanceId": "i-expired",
            "bucket": "test-bucket",
            "report_prefix": "runs/test/session/",
        }
        client = RecordingAWS()
        with (
            patch.object(client, "json", return_value={"Reservations": []}),
            self.assertRaisesRegex(RuntimeError, "no host report"),
        ):
            host.collect(args, session, client)

    def test_expiry_and_role_boundaries(self):
        template = yaml.safe_load(
            (ROOT / "packaging/cloud-test/template.yaml").read_text()
        )
        launch = template["Resources"]["Launch"]["Properties"]["LaunchTemplateData"]
        self.assertEqual(launch["InstanceInitiatedShutdownBehavior"], "terminate")
        self.assertTrue(launch["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
        self.assertEqual(launch["MetadataOptions"]["HttpTokens"], "required")
        bootstrap = launch["UserData"]["Fn::Base64"]["Fn::Sub"]
        subprocess.run(["bash", "-n"], input=bootstrap, text=True, check=True)
        self.assertLess(
            bootstrap.index("enable --now rcfp-expire.timer"),
            bootstrap.index("apt-get update"),
        )
        self.assertIn("OnCalendar=${ExpiresAt}", bootstrap)
        self.assertIn("timeout 90s", bootstrap)
        policy = template["Resources"]["Role"]["Properties"]["Policies"][0][
            "PolicyDocument"
        ]["Statement"]
        self.assertEqual(
            {row["Action"] for row in policy}, {"s3:GetObject", "s3:PutObject"}
        )
        write = next(row for row in policy if row["Action"] == "s3:PutObject")
        self.assertTrue(
            write["Resource"]["Fn::Sub"].endswith("/runs/${AWS::StackName}/*")
        )

    def test_package_and_external_http_probe(self):
        out = self.folder()
        archive, manifest, uploads = host.package(self.bundle, None, "test-bucket", out)
        self.assertEqual(len(uploads), 4)
        original = host.digest(out / "catalog.sqlite")
        with sqlite3.connect(out / "catalog.sqlite") as connection:
            paths = [row[0] for row in connection.execute("SELECT path FROM datasets")]
        self.assertTrue(
            all(path.startswith("s3://test-bucket/rasters/") for path in paths)
        )
        self.assertEqual(manifest["catalog_sha256"], original)
        expanded = out / "expanded"
        expanded.mkdir()
        with tarfile.open(archive) as packed:
            self.assertFalse(
                any("/design/" in row.name or "/build/" in row.name for row in packed)
            )
            packed.extractall(expanded, filter="data")
        with sqlite3.connect(expanded / "catalog.sqlite") as connection:
            for source, key in uploads:
                connection.execute(
                    "UPDATE datasets SET path=? WHERE path=?",
                    (str(source), f"s3://test-bucket/{key}"),
                )
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            port = temporary.getsockname()[1]
        runtime = expanded / "runtime"
        env = {
            **os.environ,
        }
        for line in remote.unit(4, [0, 2, 4, 6], "eu-north-1", "native").splitlines():
            if line.startswith("Environment="):
                setting = shlex.split(line.split("=", 1)[1])[0]
                key, value = setting.split("=", 1)
                env[key] = value.replace("/opt/rcfp/deployment/runtime", str(runtime))
        origin = f"http://127.0.0.1:{port}"
        with (out / "server.log").open("w") as log:
            process = subprocess.Popen(
                [
                    str(runtime / "bin/rcfp"),
                    "serve",
                    "-d",
                    str(expanded / "catalog.sqlite"),
                    "--bind",
                    f"127.0.0.1:{port}",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    try:
                        with urlopen(origin + "/keys", timeout=1):
                            break
                    except OSError:
                        if process.poll() is not None:
                            self.fail("server exited; inspect server.log")
                        time.sleep(0.1)
                checked = out / "client"
                checked.mkdir()
                report = probe.run(
                    origin, probe.sample_workload(), checked, 4, 2, runtime / "bin/rcfp"
                )
                self.assertTrue(report["passed"], report)
                self.assertTrue(report["image_comparisons"][0]["passed"])
                failure = out / "failed-client"
                failure.mkdir()
                failed = probe.run(
                    origin,
                    {
                        "stages": [
                            {
                                "name": "missing",
                                "paths": ["/singleband/missing/red/preview.png"],
                            }
                        ]
                    },
                    failure,
                    1,
                    1,
                )
                self.assertFalse(failed["passed"])
                self.assertEqual(failed["stages"][0]["errors"], 1)
                self.assertTrue((failure / "requests.jsonl").exists())
                invalid = out / "invalid-image"
                invalid.mkdir()
                bad_image = probe.run(
                    origin,
                    {"stages": [{"name": "invalid", "paths": ["/keys"]}]},
                    invalid,
                    1,
                    1,
                )
                self.assertFalse(bad_image["passed"])
                row = json.loads((invalid / "requests.jsonl").read_text())
                self.assertEqual(row["status"], 200)
                self.assertIn("error", row)
                tampered = out / "bad-reference"
                tampered.mkdir()
                workload = probe.sample_workload()
                workload["references"][0]["sha256"] = "0" * 64
                bad = probe.run(origin, workload, tampered, 1, 1)
                self.assertFalse(bad["passed"])
                self.assertIn("reference PNG checksum mismatch", bad["error"])
            finally:
                process.terminate()
                process.wait(timeout=30)
        self.assertEqual(host.digest(out / "catalog.sqlite"), original)

    def test_custom_catalog_is_copied(self):
        out = self.folder()
        prepare = helper(ROOT / "packaging/demo/prepare.py")
        original_dir = out / "original"
        manifest = prepare.prepare(self.bundle / "bin/rcfp", original_dir)
        database = original_dir / "catalog.sqlite"
        with sqlite3.connect(database) as connection:
            for row in manifest["fixtures"]:
                connection.execute(
                    "UPDATE datasets SET path=? WHERE band=?",
                    (str(original_dir / "data" / row["file"]), row["id"]),
                )
        before = host.digest(database)
        prepared = out / "prepared"
        prepared.mkdir()
        _, packaged, uploads = host.package(
            self.bundle, database, "test-bucket", prepared
        )
        self.assertEqual(host.digest(database), before)
        self.assertEqual(len(uploads), 4)
        self.assertEqual(
            {row["sha256"] for row in packaged["fixtures"]},
            {row["sha256"] for row in manifest["fixtures"]},
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    Contracts.bundle = args.bundle.resolve()
    Contracts.out = args.out.resolve()
    Contracts.out.mkdir(parents=True, exist_ok=False)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    host.save(
        Contracts.out / "report.json",
        {
            "passed": result.wasSuccessful(),
            "tests": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "binary_sha256": host.digest(Contracts.bundle / "bin/rcfp"),
            "aws_provisioning": "not executed",
        },
    )
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
