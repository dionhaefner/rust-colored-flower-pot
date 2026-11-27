# Deploy the public demo

## Connect to a tile server

Open the static explorer at `explore/` on the published documentation site and
enter the base URL of an RCFP or Terracotta tile API. The URL can include a path
prefix. Run `rcfp connect` to open the server picker, or pass the API URL directly:

```sh
rcfp connect https://maps.example.com/imagery
```

The browser requests catalog metadata and tiles directly from that API. The
static site needs no application server or raster storage. The tile API must
allow the viewer's origin for both metadata and tiles. The viewer supports the
Terracotta 0.10 API and discovers native capabilities when an RCFP server
advertises them. [Terracotta CORS configuration](https://terracotta-python.readthedocs.io/en/latest/settings.html#cross-origin-resource-sharing-cors)

Use `RCFP_VIEWER_URL` to point `rcfp connect` to another copy of the explorer.
To export it for static hosting, run:

```sh
python packaging/demo/export-viewer.py --out build/explore
```

For a loopback RCFP server, use `rcfp connect localhost:5000 --local` to open its
bundled explorer offline. Use `--port 5100` to serve a standalone bundled copy
for an API that allows that loopback origin. Keep the standalone process running
for the session.

With the default loopback bind, `rcfp serve` allows the project's Pages origin
for tiles unless you set `ALLOWED_ORIGINS_TILES` explicitly. The browser may ask
for local network access. For other deployments, configure both metadata and tile
CORS for the viewer's origin.

## Try the live demo

Open the [live explorer](https://dionhaefner.github.io/rust-colored-flower-pot/demo/).
GitHub Pages serves the viewer, and the
[Lambda API](https://atyp7fgt6ar4i4eddkp2jndixi0abpia.lambda-url.eu-north-1.on.aws/apidoc)
serves catalog metadata and imagery. Use **Layers** to switch bands or build an
RGB composite, and **Style** to adjust contrast and color.

## Deployment layout

The deployment can serve the bundled raster viewer alongside this documentation
on GitHub Pages. A public Lambda Function URL supplies the API, and a private S3 bucket
holds checksum-pinned cloud-optimized GeoTIFFs. The demo catalog includes native
10 m Sentinel-2 red, green, blue, and near-infrared bands over the Venice lagoon
and 30 m Copernicus elevation tiles around the Dolomites and Cape Town. The catalog
is precomputed and packaged with the function. Tile requests use database-backed S3 raster paths.
They do not discover STAC Items or stage whole STAC assets.

Infrastructure and deployment helpers live in `packaging/demo/`. Two CloudFormation
stacks separate S3/ECR storage from the function, permissions and logs. Optional
budget alerts use the global AWS Budgets API. Deployment uses the AWS CLI.

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
in a Lambda container image. The build downloads and checksum-verifies the rasters. The image retains local
copies for qualification. Tests invoke the bootstrap through AWS's runtime
emulator, decode a preview of each scene, and exercise CORS, RGB, scene switching,
and the static viewer under a Pages project path.
These checks do not establish real AWS permissions, S3 access or cloud latency.
The **Lambda demo qualification** workflow runs the same checks and retains the
image and report. It does not deploy infrastructure.

For a prepared native bundle, the runtime API adapter and browser checks can also
run without Docker. Pass `--sources PATH` to reuse a directory of rasters named
as in `tests/fixtures/navigation.json`:

```sh
python tests/demo_contracts.py --binary dist/bundle/bin/rcfp --out build/demo-native-check
```

## Deploy Lambda and S3

Install AWS CLI v2 and authenticate using your account's normal CLI credential
workflow. The deployment identity needs permission to manage CloudFormation,
S3, ECR, Lambda, IAM roles and CloudWatch Logs. Budget creation additionally
requires AWS Budgets permissions through its `us-east-1` endpoint, independently
of the deployment region. The function's own role has only raster reads
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

For a local viewer, use `--origin http://127.0.0.1:8000`. Repeat `--origin`
to allow both local and Pages origins, or add `http://127.0.0.1:5100` for
`rcfp connect --port 5100`. Plain HTTP origins are limited to loopback hosts.

This command creates billable resources and a public API. It creates storage, builds an image using digest-addressed S3 paths, and checks
it locally with the packaged rasters. It uploads those rasters to S3, pushes to
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

After AWS assigns the Function URL, the helper configures `PublicUrl` with that
HTTPS origin in a second stack update. This supplies absolute HTTPS links in
TileJSON and point discovery. Keep this parameter in saved rollback settings.

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
documentation workflow publishes the complete site, including `/explore/` and `/demo/`, only
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
Optional alerts track **all account spending before credits and refunds** at USD 5
and USD 10, so signup credits do not hide usage. They can arrive
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
demo bucket and ECR repository, then deletes the storage stack and demo budget:

```sh
python packaging/demo/remove.py --region eu-north-1 --stack rcfp-demo \
  --out build/demo-removal
```

It records the storage resource names before deletion. Remove `DEMO_API_URL` and rebuild Pages to
replace the viewer with the availability message. Deleting these resources
removes deployment images and demo raster copies, including rollback versions.
