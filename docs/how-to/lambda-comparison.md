# Compare deployed Lambda backends

`packaging/lambda-comparison/` deploys Terracotta 0.10.1 and native RCFP as separate
Lambda container functions. Both use the same precomputed SQLite bytes and
checksum-pinned rasters in a private, same-region S3 bucket. The existing demo
function is independent of these comparison functions.

The comparison fixes x86_64 architecture, 2 GiB memory, a 30-second timeout, eight
reserved invocations, one raster worker, PNG compression level 1 and seven-day
logs. Each environment has 64 MiB GDAL, 64 MiB raster-array and 32 MiB downloaded
range-cache budgets. Finished-image retention is disabled. Logical cache budgets
do not imply equal physical memory. Native source-handle reuse and Terracotta's
compressed array cache retain their normal policies.

Terracotta runs its unmodified Flask application through `serverless-wsgi` 3.1.0
on Python 3.12. Both raster runtimes report GDAL 3.10.3. This measures deployed
applications, including their transport adapters and sampling policies, rather
than an isolated language or raster-kernel comparison.

## Prepare and qualify images

Use Linux x86_64, Docker, AWS CLI credentials, and a Python 3.12 environment with
`packaging/lambda-comparison/requirements.lock` installed. The client runners
also need Pillow; the quality check needs NumPy and Rasterio. Reuse a qualified
[demo image](demo.md) as the RCFP base image, preferably its immutable ECR digest.

Acquire the pinned upstream release in a fresh directory and acquire the
[navigation corpus](../explanation/performance.md#terracotta-and-titiler-navigation-comparison):

```sh
python tests/reference/fetch.py --output build/terracotta-source
python tests/navigation_sources.py
```

Use the demo's existing private bucket and ECR repository. Preparing a catalog
uses upstream Terracotta for metadata. It preserves the original rasters and
uploads identical digest-addressed objects for both applications:

```sh
python packaging/lambda-comparison/prepare.py \
  --bucket YOUR_DEMO_BUCKET --region eu-north-1 --upload \
  --reference-source build/terracotta-source/source.tar.gz \
  --out build/comparison-input

python packaging/lambda-comparison/deploy.py \
  --region eu-north-1 --repository-uri YOUR_ECR_REPOSITORY_URI \
  --prepared build/comparison-input \
  --rcfp-base-image YOUR_QUALIFIED_RCFP_IMAGE \
  --out build/comparison-deployment
```

The helper builds both images and invokes the official Lambda emulator before
pushing them. It verifies identical S3-backed catalogs, repeated independent
preview pixels, full-scene singleband/RGB responses and error handling. Local
tests mount the same pinned rasters without AWS credentials. Deployment requires
the same CloudFormation, IAM, Lambda, S3 and ECR permissions as the demo. Images
remain pinned by digest. The shared demo bucket and repository remain separately
owned storage resources.

The output records both Function URLs, stack IDs, image digests, catalog/source
checksums and function settings. Use fresh output directories for each run.

## Measure map navigation in a browser

Install Playwright and its Chromium runtime in the client environment:

```sh
python -m pip install playwright==1.55.0
python -m playwright install chromium
python tests/lambda_map_benchmark.py \
  --deployment build/comparison-deployment/deployment.json \
  --trials 3 --out build/lambda-map-comparison
```

This runner opens a 1600×1000 Leaflet map on the full Sentinel scene. Each map
loads a first viewport at zoom 12, pans 512 pixels horizontally and 128 vertically,
zooms to 13 then 14, pans at detail scale, zooms out and revisits its initial
center. Leaflet retains its normal tile buffer, browser cache and connection
reuse. There is no fixed client request-concurrency setting. The report records
actual tile requests and simultaneous requests on the wire separately from
browser requests waiting for connections.

Each action is timed in the browser until all current tiles load, map animation
and tile fades finish, visible images decode and two animation-frame callbacks
run. Screenshots record the resulting map. This is a complete-map readiness
metric; it does not read a compositor presentation timestamp. Local map assets
load before timing. No external basemap is requested.

Three paired trials rotate backend order for singleband and RGB. Every map has
a new browser context and a fresh function generation before its first image.
Later pans and zooms can still initialize additional Lambda environments.
The runner temporarily raises both reserved-invocation ceilings to 64 to avoid
the demo's eight-invocation ceiling limiting a viewport burst. The account must
have enough available concurrency. Original environments and concurrency settings
are restored in the final cleanup, including after interruption. Keep the full
failed run if any tile, map action or instrumentation check fails.

## Measure scripted tile batches

```sh
python tests/lambda_benchmark.py \
  --deployment build/comparison-deployment/deployment.json \
  --trials 4 --out build/lambda-comparison
```

Each paired trial changes only the comparison functions' environment generation
to request fresh execution environments. The first image viewport is measured
before health or warmup requests. A fresh-function viewport can contain both
initializing and reused environments; it is not a physically cold S3 experiment.
CloudWatch REPORT records identify actual initialization and server duration by
request ID. Missing log records and initialization during post-warmup traffic
are reported separately.

Subsequent cases cover nine-tile full-scene singleband/RGB views at zooms 11, 13
and 14, pans and revisits, with one or four concurrent client requests. Each
navigation sequence runs once as warmup before measurement. Backend order rotates
by trial and case. Timed requests are not retried. Completion timing ends at the
last HTTP response receipt; image decode and recording follow separately. The
runner restores the original function environments when it finishes.

The report retains individual requests, failures, decoded image hashes, server
duration, initialization, memory observations and paired stage ratios. Ratios
are computed within each trial before aggregation; failed pairs have no ratio.
Missing server records, initialization during measured traffic, request errors
or failed configuration restoration prevent a passing report. Four paired trials show a
small sample of cloud variability, not a general performance guarantee. The
client's network and fresh HTTP connections contribute to these measurements.
See the [measured Lambda example](../explanation/performance.md#deployed-lambda-comparison)
for results and its excluded trials.

Run the independent image assessment with a complete native GDAL bundle:

```sh
python tests/lambda_benchmark_quality.py \
  --results build/lambda-map-comparison --runtime PATH_TO_NATIVE_BUNDLE \
  --out build/lambda-quality
```

It reads the original pinned pixels through GDAL's public output-grid warp API,
including automatic overview selection, and records exact pixels, mask
disagreements and RGB differences for each backend. These generated references
stay in the report directory and do not replace maintained golden fixtures.
Sampling differences require their own interpretation alongside timing ratios.

## Remove comparison resources

The comparison uses the account-wide demo budget alerts and creates billable
storage, requests and execution. Neither concurrency nor budget alerts establish
a spending cap. No provisioned concurrency, API Gateway, VPC or NAT gateway is
created.

Delete the two recorded CloudFormation stack IDs after testing when the endpoints
are no longer needed:

```sh
aws cloudformation delete-stack --region eu-north-1 --stack-name NATIVE_STACK_ID
aws cloudformation delete-stack --region eu-north-1 --stack-name TERRACOTTA_STACK_ID
```

Retain reports before deleting resources. Shared images and S3 rasters remain
until explicitly removed. The demo removal helper deletes its shared repository
and bucket, so remove dependent comparison functions before removing that storage.
