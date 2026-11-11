"""Install the paired EC2 servers from qualified image digests and pinned catalogs."""

import argparse
import hashlib
import json
import runpy
import shlex
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    session = json.loads(args.session.read_text())
    deployment = json.loads(args.deployment.read_text())
    cfg = {
        "region": session["region"],
        "bucket": session["bucket"],
        "result_prefix": f"runs/{session['stack']}/comparison/",
        "manifest": deployment["manifest"],
        "images": {
            e: f["parameters"]["ImageUri"] for e, f in deployment["functions"].items()
        },
    }
    prepared = args.prepared / "catalog.sqlite"
    assert (
        hashlib.sha256(prepared.read_bytes()).hexdigest()
        == cfg["manifest"]["catalog_sha256"]
    )
    (args.out / "configuration.json").write_text(json.dumps(cfg, indent=2) + "\n")
    archive = args.out / "setup.tar.gz"
    with tarfile.open(archive, "w:gz") as packed:
        for source, name in (
            (HERE / "remote.py", "remote.py"),
            (HERE / "profile_app.py", "profile_app.py"),
            (prepared, "catalog.sqlite"),
            (args.out / "configuration.json", "configuration.json"),
        ):
            packed.add(source, arcname=name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    key = f"artifacts/ec2-comparison/{digest}/setup.tar.gz"
    AWS = runpy.run_path(str(ROOT / "packaging/cloud-test/host.py"))["AWS"]
    client = AWS(session["region"])
    client.call(
        "s3",
        "cp",
        str(archive),
        f"s3://{session['bucket']}/{key}",
        "--only-show-errors",
    )
    download = f"import boto3; boto3.client('s3',region_name={session['region']!r}).download_file({session['bucket']!r},{key!r},'/opt/rcfp/setup.tar.gz')"
    script = f"""set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io nginx python3-psutil linux-tools-common linux-tools-$(uname -r)
systemctl stop nginx
systemctl enable --now docker
mkdir -p /opt/rcfp/comparison
python3 -c {shlex.quote(download)}
echo '{digest}  /opt/rcfp/setup.tar.gz' | sha256sum -c -
tar -xzf /opt/rcfp/setup.tar.gz -C /opt/rcfp/comparison
openssl req -x509 -nodes -newkey rsa:2048 -keyout /opt/rcfp/comparison/key.pem -out /opt/rcfp/comparison/certificate.pem -days 1 -subj /CN=rcfp-ec2-comparison
python3 /opt/rcfp/comparison/remote.py setup
"""
    (args.out / "bootstrap.sh").write_text(script)
    print(
        client.command(
            session["InstanceId"], script, args.out / "bootstrap.json", timeout=900
        )
    )


if __name__ == "__main__":
    main()
