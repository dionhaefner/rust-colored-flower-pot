"""Install an isolated browser load generator without copying local AWS credentials."""

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
    parser.add_argument("--client-session", type=Path, required=True)
    parser.add_argument("--server-session", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--aws-dist", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    session = json.loads(args.client_session.read_text())
    archive = args.out / "client.tar.gz"
    files = [
        "tests/map_sessions.py",
        "tests/ec2_user_benchmark.py",
        "tests/map_session_contracts.py",
        "tests/lambda_map_benchmark.py",
        "tests/lambda_benchmark.py",
        "packaging/cloud-test/host.py",
        "packaging/ec2-comparison/remote.py",
        "tests/benchmarks/map-session.html",
        "src/assets/client/vendor/leaflet.js",
        "src/assets/client/vendor/leaflet.css",
    ]
    with tarfile.open(archive, "w:gz") as packed:
        for name in files:
            packed.add(ROOT / name, arcname=name)
        packed.add(args.aws_dist, arcname="aws-cli-dist")
        packed.add(args.server_session, arcname="server-session.json")
        packed.add(args.client_session, arcname="client-session.json")
        packed.add(args.deployment, arcname="deployment.json")
        packed.add(HERE / "Dockerfile.client", arcname="client-image/Dockerfile")
        packed.add(
            HERE / "requirements-client.lock",
            arcname="client-image/requirements-client.lock",
        )
        packed.add(HERE / "network.py", arcname="network.py")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    key = f"artifacts/ec2-comparison-client/{digest}/client.tar.gz"
    AWS = runpy.run_path(str(ROOT / "packaging/cloud-test/host.py"))["AWS"]
    client = AWS(session["region"])
    client.call(
        "s3",
        "cp",
        str(archive),
        f"s3://{session['bucket']}/{key}",
        "--only-show-errors",
    )
    download = f"import boto3; boto3.client('s3',region_name={session['region']!r}).download_file({session['bucket']!r},{key!r},'/opt/rcfp/client.tar.gz')"
    script = f"""set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io iproute2
systemctl enable --now docker
mkdir -p /opt/rcfp/map-study
python3 -c {shlex.quote(download)}
echo '{digest}  /opt/rcfp/client.tar.gz' | sha256sum -c -
tar -xzf /opt/rcfp/client.tar.gz -C /opt/rcfp/map-study
docker build -t rcfp-map-client /opt/rcfp/map-study/client-image
docker image inspect rcfp-map-client > /opt/rcfp/map-study/client-image.json
lscpu --json > /opt/rcfp/map-study/client-cpu.json
uname -a > /opt/rcfp/map-study/client-kernel.txt
/opt/rcfp/map-study/aws-cli-dist/aws --version
"""
    (args.out / "bootstrap.sh").write_text(script)
    (args.out / "artifact.json").write_text(
        json.dumps(
            {"bucket": session["bucket"], "key": key, "sha256": digest}, indent=2
        )
        + "\n"
    )
    print(
        client.command(
            session["InstanceId"], script, args.out / "bootstrap.json", timeout=1200
        )
    )


if __name__ == "__main__":
    main()
