# Test a disposable cloud deployment

Use `packaging/cloud-test/host.py` to create an on-demand Ubuntu 24.04 EC2 host,
deploy a Linux native bundle, collect reports and remove the host. Infrastructure
is defined in CloudFormation. A separate private S3 bucket retains immutable
raster copies, deployment archives and reports between sessions.

The default host is `c7i.2xlarge`, with four physical cores and 16 GiB memory.
The native server uses four raster workers, with process affinity to one hardware
thread per core, with 64 MiB array, 64 MiB GDAL and 32 MiB curl cache budgets,
and no finished-image retention. Run the
request client on your laptop to include the real client network. The small
bundled raster samples provide an installation check; use representative full
rasters for performance qualification.

## Check without credentials

Prepare a complete x86_64 Linux [native bundle](../reference/artifacts.md) that
runs on Ubuntu 24.04. The AL2023 bundle also targets an older glibc baseline.
Keep its `bin`, `lib`, `share` and `licenses` directories together.

For a bundle built against AL2023 using Docker:

```sh
docker build --platform linux/amd64 \
  -f packaging/lambda/Dockerfile.ci -t rcfp-cloud-build .
container_id=$(docker create rcfp-cloud-build)
mkdir -p build
docker cp "$container_id:/bundle" build/cloud-bundle
docker rm "$container_id"
```

Use `build/cloud-bundle` in place of `dist/bundle` below. Successful qualification
workflow runs also retain a `cloud-native-bundle-COMMIT` artifact containing the
checked bundle and its archive checksum.

```sh
python -m pip install cfn-lint==1.40.4 pillow==11.3.0 playwright==1.55.0
python -m playwright install chromium
cfn-lint packaging/cloud-test/storage.yaml packaging/cloud-test/template.yaml
python tests/cloud_test_contracts.py \
  --bundle dist/bundle --out build/cloud-test-local
```

The checks cover catalog copying and S3 path preparation, archive extraction,
HTTP image decoding and independent preview fidelity, viewport requests, the
browser, and simulated provisioning/collection failures. They do not invoke AWS.
The **Disposable cloud host qualification** workflow builds a native bundle and
runs these checks on Ubuntu. Cloud provisioning needs separate execution evidence.

## Create a session

Install AWS CLI v2 and authenticate through your normal CLI credential workflow.
The deployment identity needs CloudFormation, EC2 networking/instance, IAM
role/profile, S3 and Systems Manager permissions, including `iam:PassRole`.
Reading the default image needs `ssm:GetParameter`; estimating compute needs
`pricing:GetProducts` against the pricing service in `us-east-1`. Interactive
shell access additionally needs the Session Manager plugin.

Choose a region and your current public IPv4 address. The security group permits
port 5000 only from that address; administration uses SSM without an SSH key.

```sh
python packaging/cloud-test/host.py up \
  --region eu-north-1 --client-cidr YOUR_PUBLIC_IPV4/32 \
  --dry-run --out build/cloud-plan

python packaging/cloud-test/host.py up \
  --region eu-north-1 --client-cidr YOUR_PUBLIC_IPV4/32 \
  --ttl-hours 4 --out build/cloud-session
```

`--dry-run` performs read-only pricing and image lookups. The real command prints
the same compute estimate before creating billable resources. It resolves the
official Canonical Ubuntu 24.04 image and records its exact ID. Supply `--ami-id`
to reuse that image in another session. If your identity cannot query prices,
provide a verified regional compute rate through `--hourly-usd`.

The estimate covers Linux on-demand compute only. EBS, public IPv4, S3 storage and
requests, transfer and tax are additional. A same-region S3 gateway endpoint
avoids EC2–S3 transfer charges and has no endpoint fee. This setup creates no NAT
gateway or load balancer. Retained S3 storage continues to incur charges after
host removal. See [EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/)
and [S3 pricing details](https://aws.amazon.com/s3/faqs/).

`build/cloud-session/session.json` identifies the instance, immutable stack ID,
bucket and expiry. Preserve it for subsequent commands. Output directories must
be new. A failed startup attempts to remove its newly created host stack and
leaves retained storage available. An existing host stack must be removed before
creating another with the same name.

## Deploy and check

Deploy the bundled Sentinel-2 samples first:

```sh
python packaging/cloud-test/host.py deploy \
  --session build/cloud-session/session.json \
  --bundle dist/bundle --out build/cloud-deploy

python tests/cloud_deployment.py \
  --session build/cloud-session/session.json \
  --viewer-binary dist/bundle/bin/rcfp --out build/cloud-check
```

Deployment uploads digest-addressed rasters and an archive, verifies the archive
checksum on the host, installs a systemd service and checks discovery locally.
The instance role reads only its test bucket's rasters/artifacts and writes only
its host's report prefix. IMDSv2 supplies temporary role credentials; no keys are
copied to the host. Real S3 reads and role permissions are exercised by the image
requests in the second command.

The request check records stage completion times, request p95, failures, decoded
image hashes and PNGs. Its default preview must match the independent pinned
Terracotta reference. `--viewer-binary` starts the bundled viewer locally and
uses Chromium to check first view, pan, zoom and revisit. Omitting it records the
browser as unchecked. The API uses HTTP for this restricted temporary test;
open the viewer locally with `rcfp connect API_URL`, rather than through the
HTTPS documentation site.

## Use representative inputs

Pass `--catalog PATH` to `deploy` to copy a precomputed Terracotta-format SQLite
catalog. Its datasets must reference absolute local paths to self-contained TIFFs
with embedded masks/overviews. Deployment hashes and uploads those files, rewrites
paths in the copied catalog to private S3 objects and records their checksums.
The original catalog is preserved. External mask/overview/auxiliary files, VRTs,
stored native bindings and STAC source catalogs require their own staging and are
not handled by this catalog-copy helper. Large artifacts must fit the 40 GiB host
disk. For a smaller `--instance-type c7i.xlarge` host, deploy with `--workers 2`.

Describe representative viewport requests in a JSON workload:

```json
{
  "scope": "Representative Sentinel-2 viewport",
  "stages": [
    {
      "name": "viewport",
      "paths": [
        "/singleband/scene/red/13/4377/2930.png?stretch_range=%5B0%2C3000%5D"
      ]
    }
  ],
  "references": []
}
```

Each stage submits its PNG paths with bounded concurrency; stages run in order.
Add viewport, pan, zoom and revisit paths appropriate to your catalog. An optional
reference entry contains `path`, `file` and `sha256`, where `file` is an independent
reference PNG relative to the workload file. Without references, the report
records image acceptance as decode-only. Never acquire goldens from RCFP output.

```sh
python tests/cloud_deployment.py \
  --session build/cloud-session/session.json \
  --workload build/workload.json --concurrency 4 --repeats 3 \
  --viewer-binary dist/bundle/bin/rcfp --out build/cloud-workload
```

The first pass and subsequent passes are identified separately; this does not
claim cold S3 or disk caches. Requests use Python's HTTP client with fresh
connections. Browser timing includes its own connection and image-loading
behavior. Running the viewer changes server caches, so it follows the timed HTTP
workload. Interpret timing differences within this deployment and workload;
these checks do not repeat the controlled three-engine comparison.

## Collect and remove

```sh
python packaging/cloud-test/host.py collect \
  --session build/cloud-session/session.json --out build/cloud-reports
python packaging/cloud-test/host.py down \
  --session build/cloud-session/session.json --out build/cloud-teardown
```

Collection retains server configuration/hashes, CPU details, journal output,
application metrics and five-second samples of process RSS, peak RSS, CPU time
and thread count. Keep the separate local client report alongside these files.
`down` attempts collection and stops the server before deleting the host stack.
Collection failure is reported separately and does not prevent host deletion.

The expiry timer uses an absolute UTC deadline, so rebooting does not extend the
session. It attempts collection with a 90-second limit and then powers off; the
instance is configured to terminate and delete its root disk on shutdown.
This depends on successful timer installation and a functioning guest OS;
it is not an account spending cap. After automatic termination, `collect` can
download previously uploaded reports, and `down` removes the remaining network
and IAM resources. If installation or collection failed, missing reports are
reported as failures. Expiry never deletes retained S3 data.

Use SSM for inspection or to run a client against another deployment:

```sh
python packaging/cloud-test/host.py shell --session build/cloud-session/session.json
python packaging/cloud-test/host.py exec \
  --session build/cloud-session/session.json \
  --command 'uname -a' --out build/cloud-command
```

The VM can host a request client against Lambda by installing that client's
dependencies and copying its workload through SSM/S3. Keep that run separate
from testing RCFP on the VM. The `exec` command retains SSM output locally.

To delete all retained test rasters, artifacts and reports after host teardown:

```sh
python packaging/cloud-test/host.py purge-storage \
  --session build/cloud-session/session.json --out build/cloud-storage-removal
```

Download any reports you need before purging. The helper checks the recorded
bucket against the storage stack output and refuses to purge while its host
stack still exists. This storage is independent of the public Lambda demo.
