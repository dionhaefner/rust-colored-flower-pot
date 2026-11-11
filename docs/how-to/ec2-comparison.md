# Compare map navigation and profile EC2 servers

`packaging/ec2-comparison/` runs native RCFP and Terracotta 0.10.1 in turn on one
disposable EC2 host. It reuses the qualified images and pinned S3 catalog from
the [Lambda comparison](lambda-comparison.md). Both servers have two raster
workers, the same two logical CPU IDs and a 2 GiB container memory limit.
Terracotta uses two Gunicorn 23.0.0 sync workers with internal multiprocessing
disabled. The host's CPU topology is recorded; two hardware threads on one core
are not two physical cores.

Each service has aggregate logical budgets of 64 MiB GDAL blocks, 64 MiB raster
arrays and 32 MiB downloaded ranges. Terracotta divides those budgets between
its workers. Finished-image caching is off. Native source leases and
Terracotta's compressed-array cache retain their normal policies. The budgets
do not imply equal physical memory or eviction behavior.

## Prepare a temporary host

Use the Docker-qualified images and prepared catalog from the Lambda procedure.
The local controller needs AWS CLI credentials, Pillow, Playwright and Chromium.
The instance role reads the selected S3 bucket and ECR repository and writes its
own report prefix. No long-lived AWS keys are copied into a container.

```sh
python packaging/cloud-test/host.py up \
  --region eu-north-1 --stack rcfp-ec2-comparison \
  --client-cidr YOUR_PUBLIC_IPV4/32 --ttl-hours 3 \
  --instance-type c7i-flex.large --data-bucket YOUR_DEMO_BUCKET \
  --image-repository-arn YOUR_ECR_REPOSITORY_ARN \
  --out build/ec2-session

python packaging/ec2-comparison/setup.py \
  --session build/ec2-session/session.json \
  --deployment build/comparison-deployment/deployment.json \
  --prepared build/comparison-input --out build/ec2-setup
```

Choose a host allowed by the account and suitable for the intended deployment.
Flex hosts have variable CPU capacity and do not reproduce a four-core local
benchmark. The disposable-host helper records the current regional compute
estimate and installs an expiry timer. Its [cost and removal procedures](cloud-test.md)
apply here too. Shared demo storage remains owned by the demo; the host helper
refuses to purge a bucket supplied through `--data-bucket`.

Setup pulls exact image digests, installs Gunicorn, verifies local copies of the
selected full-scene COGs and prepares a separate local-path catalog for diagnostics.
Both servers otherwise use the original S3-backed catalog. A common nginx TLS
proxy accepts traffic on port 5000 from the configured client address only.
The benchmark uses a temporary self-signed certificate accepted explicitly by
its browser context. It is not a production HTTPS configuration.

## Measure the complete map

```sh
python tests/ec2_map_benchmark.py \
  --session build/ec2-session/session.json \
  --deployment build/comparison-deployment/deployment.json \
  --trials 3 --out build/ec2-map-s3
```

This drives the same 1600×1000 Leaflet fixture, centers, pan distances, zooms,
styles and seven map actions as the Lambda browser test. Browser scheduling,
connection reuse and retained map tiles determine parallelism. Each new map
starts a fresh server after a metadata-only readiness check. Server startup is
outside first-view timing. Unlike fresh Lambda generations, the first view does
not include runtime initialization. Cold disk or cold S3 service caches are not
claimed.

Times include current-tile loading, map animation, tile fades, image decode and
two animation-frame callbacks. The report also records readiness/decode markers,
individual browser request timings, IDs and screenshots. Proxy logs join those
IDs to upstream response times. Upstream time includes application queueing,
raster access and encoding; it is not isolated CPU time. Keep failed requests
and incomplete actions in the report rather than retrying them out of the data.

## Diagnose fixed overhead and raster access

Run separate diagnostic comparisons with fresh output directories:

```sh
python tests/ec2_map_benchmark.py \
  --session build/ec2-session/session.json \
  --deployment build/comparison-deployment/deployment.json \
  --trials 3 --animations off --out build/ec2-map-no-animation

python tests/ec2_map_benchmark.py \
  --session build/ec2-session/session.json \
  --deployment build/comparison-deployment/deployment.json \
  --trials 3 --source local --out build/ec2-map-local

python tests/ec2_map_benchmark.py \
  --session build/ec2-session/session.json \
  --deployment build/comparison-deployment/deployment.json \
  --trials 1 --profile --out build/ec2-map-profile

python tests/ec2_map_benchmark.py \
  --session build/ec2-session/session.json \
  --deployment build/comparison-deployment/deployment.json \
  --trials 1 --perf-only --out build/ec2-map-perf
```

Disabling animation removes Leaflet's visual transition policy while retaining
image decoding and frame callbacks. Local-source runs use the original pinned
COG bytes through the host's warm file cache. Comparing them with S3 helps
separate source-access costs, but does not measure physically cold storage.
Animation policy also affects Leaflet's tile retention. Check issued request
paths and counts before treating an animation comparison as a timing-only
intervention. A retained tile can complete without any server request.

Profile runs set `TC_PROFILE_RASTER=1`, collect cumulative per-worker Terracotta
`cProfile` files and enable Linux `perf` CPU-clock stack sampling. Native timing
messages depend on the executed raster path; inspect the logs before claiming
per-stage measurements. Profile runs are separate from headline timings because
instrumentation changes execution cost. Python
call-profile times include waits inside C calls. CPU samples identify active
code, not waiting time. Record unsupported sampling or missing symbols as
limitations. Before/after process, cgroup and host counters record CPU time,
memory observations, quota throttling and host steal time.
Use `--perf-only` for a separate sampling run with ordinary application code
and no Python call tracer. The two profiling options are mutually exclusive.

For independent imagery checks, use the same GDAL reference assessment:

```sh
python tests/lambda_benchmark_quality.py \
  --results build/ec2-map-s3 --runtime PATH_TO_NATIVE_BUNDLE \
  --out build/ec2-map-quality
```

## Measure multiple users and interrupting navigation

The session workload uses independent browser contexts on a fixed wall-clock
schedule. Each user pans every 2.2 seconds, then issues two bursts of 12 view
changes 300 ms apart, followed by six band/stretch or palette changes. Users'
paced inputs are staggered. Inputs continue when previous tiles are unfinished.
The latest requested zoom is retained while an existing animation finishes.
The first style change also recenters the map; the remaining five keep that view.

```sh
python tests/ec2_user_benchmark.py \
  --session build/ec2-session/session.json \
  --deployment build/comparison-deployment/deployment.json \
  --users 1 4 8 --trials 3 --out build/ec2-users
```

The default arms are native RCFP, Terracotta with level-9 array-cache compression,
and Terracotta with the supported compression setting reduced to level 1.
Packages, CPU, worker counts, memory and aggregate logical cache budgets match.
The compression change also changes how many arrays fit in the budget.

The comparison helper initially configures a 64-job native raster queue. RGB
tiles can submit three band jobs each, so a browser burst can exhaust that queue
before either worker finishes. Use `--native-queue-capacity 256` to measure that
supported tuning separately. Preserve failures from the smaller queue and record
the selected capacity. More queue space does not add CPU capacity or guarantee
shorter waits. `--continue-on-case-error` records a failed case and proceeds to
the other arms; the overall command still fails if any case fails.
Command success records completed measurement without JavaScript exceptions.
Assess tile failures, client scheduling validity and deadline fractions separately.

Viewport coverage samples every 50 ms and counts decoded successful tiles at
the requested zoom and full opacity. Transparent tiles count as loaded. This
measures requested-resolution tile coverage, not the semantic usefulness of each
pixel or a compositor presentation timestamp. Completion requires the intended
center/zoom, two ready samples and no active map animation. Report actual sample
gaps and input scheduling delays to detect a saturated load generator.

The primary summaries include the fraction of targets reached within 0.5, 1,
1.5 and 2 seconds, requested-resolution coverage deficits, and recovery after the
last input in a burst. A paced target is censored when the next scheduled input
supersedes it. An unfinished target remains in every deadline denominator; if
enough targets are censored, p95 has no finite measured value. Preserve HTTP
failures, transport errors and expected canceled requests as separate outcomes.
Chromium can block a cross-origin JSON error body before exposing its HTTP status
to the image request listener. Check proxy logs when an image fails with
`ERR_BLOCKED_BY_ORB`; it can represent a server 503 rather than a transport fault.
Requests without a terminal CDP event before browser closure remain unresolved
observations. Do not count them as successful responses or inferred cancellations.

Use `prepare.py --navigation-controls` from the Lambda comparison directory to
include the pinned elevation and land-cover COGs in a common catalog. Point the
EC2 deployment manifest at that catalog and run `--scene dem --modes singleband` or
`--scene worldcover --modes singleband`. Land cover uses nearest sampling and
explicit category palettes. Sentinel and elevation use linear sampling.
The session runner supports `--spec FILE` for a recorded alternative schedule.
Use `--arrangement disjoint` for spatially separated user viewports. The default
`overlapping` arrangement displaces users by less than a viewport and allows
substantial reuse. Each disjoint user's initial map is also spatially separated.
Initial readiness has a separate 120-second deadline before the fixed schedule.

For an isolated cloud browser client, create another disposable host using
`host.py up --command-target-arn SERVER_INSTANCE_ARN
--command-reports-prefix runs/SERVER_STACK/comparison/`. Those paired arguments
grant control of that server through SSM and read access to its report prefix.
The client's own role writes only its own result prefix. Restrict the server's
port 5000 ingress to the client address. Install the browser runtime with
`packaging/ec2-comparison/setup_client.py`, supplying `--client-session`,
`--server-session`, `--deployment`, `--aws-dist` and `--out`. The AWS distribution
directory contains executable files only; credentials come from the instance
role. Run the controller inside the resulting `rcfp-map-client` image with host
networking and the installed workspace mounted at `/workspace`.

`packaging/ec2-comparison/network.py --server-ip ADDRESS --out FILE` records and
applies 20 ms delay in each direction and a 1000 Mbit/s aggregate rate to traffic
between that disposable client and server. Run it on the disposable client only.
Preserve its report, actual browser scheduling gaps and instance topology.
Qualify the largest user cohort before comparing servers. A small client can
saturate while decoding tiles and sampling coverage even when the service is
healthy. The client host needs its own finite expiry and explicit removal.

After browser timing is finished, assess sampled imagery:

```sh
python tests/map_session_quality.py \
  --results build/ec2-users --runtime PATH_TO_NATIVE_BUNDLE \
  --out build/ec2-users-quality
```

## Collect and remove the host

The runner stops the servers, uploads reports through the instance role and
copies them into its local output directory. Preserve those results and the
session file. Then remove the host stack:

```sh
python packaging/cloud-test/host.py down \
  --session build/ec2-session/session.json --out build/ec2-removed
```

Deletion removes the instance, network, role and root disk. The shared demo
bucket and ECR repository remain. If the expiry timer terminated the instance
first, remove its remaining stack with the same command. Never interpret an
expired or interrupted run as a successful benchmark.
