# Deploy the public demo

The demo serves the bundled raster viewer alongside this documentation on GitHub
Pages. A public Lambda Function URL supplies the API, and a private S3 bucket
holds four checksum-pinned Sentinel-2 crops. The catalog is precomputed and
packaged with the function. Tile requests use database-backed S3 raster paths.
They do not discover STAC Items or stage whole STAC assets.

Infrastructure and deployment helpers live in `packaging/demo/`. Two CloudFormation
stacks separate S3/ECR storage from the function, permissions, logs and optional
budget alerts. Deployment uses the AWS CLI.

## Build and check without credentials

Use an x86_64 Linux host with Docker, Python 3.12+, and the test dependencies:

```sh
python -m pip install cfn-lint==1.40.4 pillow==11.3.0 playwright==1.55.0
python -m playwright install chromium
cfn-lint packaging/demo/storage.yaml packaging/demo/template.yaml
bash packaging/demo/build.sh
python tests/demo_contracts.py --image rcfp-demo --out build/demo-check
```

The build uses the existing AL2023 native build and packages its GDAL/PROJ runtime
in a Lambda container image. The credential-free image includes local copies of
the same rasters. Tests invoke the bootstrap through AWS's runtime emulator,
compare decoded imagery with an independent pinned golden, and exercise CORS,
discovery, errors, RGB and the static viewer under a Pages project path.
These checks do not establish real AWS permissions, S3 access or cloud latency.
The **Lambda demo qualification** workflow runs the same checks and retains the
image and report. It does not deploy infrastructure.

For a prepared native bundle, the runtime API adapter and browser checks can also
run without Docker:

```sh
python tests/demo_contracts.py --binary dist/bundle/bin/rcfp --out build/demo-native-check
```

## Deploy Lambda and S3

Install AWS CLI v2 and authenticate using your account's normal CLI credential
workflow. The deployment identity needs permission to manage CloudFormation,
S3, ECR, Lambda, IAM roles and CloudWatch Logs. Budget creation additionally
requires AWS Budgets permissions. The function's own role has only raster reads
and invocation log writes. Keep credentials outside the repository.

Choose one AWS region and the browser **origin**, which excludes the Pages project
path. For `https://your-user.github.io/your-project/demo/`, use
`https://your-user.github.io`:

```sh
python packaging/demo/deploy.py \
  --region eu-north-1 \
  --origin https://your-user.github.io \
  --budget-email you@example.com \
  --out build/demo-deployment
```

This command creates billable resources and a public API. It creates storage,
uploads immutable digest-addressed rasters, builds an image using those S3 paths,
and checks it locally with the packaged copies of the rasters. It then pushes to
ECR, deploys the function, and checks discovery, CORS and a decoded PNG through
the actual Function URL. Docker login stores an expiring ECR token using Docker's
configured credential storage.

The output gives `ApiUrl` and `FunctionName`. The output directory retains the
image digest, stack outputs and deployment parameters. Keep it for rollback and
qualification. Use a fresh output directory for each update. The default stack
names are `rcfp-demo` and `rcfp-demo-storage`. Stack names are limited to 50 characters.

Both invocation permissions required by public Function URLs are in the template.
RCFP supplies CORS headers itself, with anchored allowed-origin patterns. The URL
resource supplies no additional CORS configuration, avoiding duplicate headers.
CORS does not authenticate callers or restrict command-line access.

Some new AWS accounts have concurrency quotas too small to reserve the default
eight invocations while leaving AWS's required unreserved capacity. Check the
regional quota and request an increase if necessary. Account eligibility and
quotas need actual credentials.

## Publish the viewer alongside the docs

Configure GitHub Pages to build from Actions, then set repository variables using
the GitHub CLI:

```sh
gh api --method POST repos/OWNER/REPO/pages -f build_type=workflow
gh variable set DEMO_API_URL --body 'https://YOUR_FUNCTION.lambda-url.REGION.on.aws/'
gh variable set PUBLISH_DOCS --body true
gh workflow run docs.yml --ref main
```

For an existing Pages site, use `--method PUT` to update its build type. The
documentation workflow publishes the complete site, including `/demo/`, only
from `main` when `PUBLISH_DOCS=true`. With no API URL configured, `/demo/` displays
an availability message and deployment instructions. Changing the URL requires
rebuilding the site. Keep the deployed backend and viewer revision compatible.

For a local static export:

```sh
python packaging/demo/export-viewer.py \
  --api-url https://YOUR_FUNCTION.lambda-url.REGION.on.aws/ \
  --revision YOUR_PUBLIC_DATA_REVISION \
  --out build/site/demo
python -m http.server --directory build/site 8000
```

The exporter uses relative asset and module paths, so it also works beneath a
GitHub Pages repository prefix. Add a localhost origin to the backend configuration
before testing cross-origin calls from a local site.

## Costs and operation

The defaults use on-demand Lambda with 2 GiB memory, a 30-second invocation
timeout, one raster worker per environment, eight concurrent environments and
seven-day log retention. No provisioned concurrency, VPC, NAT gateway or API
Gateway is provisioned. AWS supplies temporary execution-role credentials for S3.
Each environment has its own source handles and caches. Scale-out does not share
those caches. The supplied imagery covers a small lagoon near Venice and carries
Copernicus attribution in the viewer and metadata.

The [Lambda free tier](https://aws.amazon.com/lambda/pricing/) is shared with other
account usage. S3 storage/requests, ECR image storage, logs and transfer can incur
separate charges. Concurrency bounds simultaneous work, not monthly spend.
Optional alerts track **all account spending** at USD 5 and USD 10. They can arrive
after charges accrue and do not stop execution. This configuration is not a hard
USD 10 spending cap. Retained images and raster revisions also consume storage.

Disable all invocations immediately with:

```sh
aws lambda put-function-concurrency --region eu-north-1 \
  --function-name rcfp-demo --reserved-concurrent-executions 0
```

Restore the chosen concurrency with the same command. Reconcile manual changes
with the CloudFormation `Concurrency` parameter before the next deployment.

Check initial and warm map views, RGB, expressions and errors on the deployed
site, and retain timings with the image digest and source checksums. The small
demo crops validate deployment behavior. They do not represent full-scene
remote performance. Keep separate benchmark results for larger representative
rasters and avoid treating warm invocation caches as shared across environments.

CloudFormation rolls back failed infrastructure updates. To restore an earlier
successful image, redeploy `template.yaml` with the saved values from its
`parameters.json`. Images are pinned by digest and are retained in ECR until
explicitly removed. A failure in the post-deployment HTTP checks does not
automatically roll back an otherwise successful stack update.

## Remove the deployment

The removal helper disables invocations, deletes the runtime stack, empties the
demo bucket and ECR repository, then deletes the storage stack:

```sh
python packaging/demo/remove.py --region eu-north-1 --stack rcfp-demo \
  --out build/demo-removal
```

It records the storage resource names before deletion. Remove `DEMO_API_URL` and rebuild Pages to
replace the viewer with the availability message. Deleting these resources
removes deployment images and demo raster copies, including rollback versions.
