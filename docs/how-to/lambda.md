# Package the native Lambda runtime

RCFP supplies a custom-runtime bootstrap and API Gateway v1/v2 event adapters.
Build a Lambda-targeted native bundle for the intended architecture and Amazon
Linux baseline; a desktop wheel or arbitrary host-built bundle is not a Lambda
artifact. See [AWS custom runtime requirements](https://docs.aws.amazon.com/lambda/latest/dg/runtimes-custom.html)
and [the support matrix](../reference/support.md). Local runtime-image tests do
not establish an actual account deployment.

From the repository root, with a prepared Lambda bundle:

```sh
packaging/build-lambda.sh dist/lambda-bundle dist/rcfp-lambda.zip
```

The ZIP contains executable `bootstrap` and `runtime/`. The bootstrap configures
native libraries/data and starts `rcfp lambda-runtime`, which communicates with
the Lambda Runtime API. For an AWS deployment, select the provided Amazon Linux
2023 runtime and matching architecture, then configure `TC_DRIVER_PATH` and any
provider settings. Grant the execution role access only to the intended catalog
and raster locations. Set service memory/time limits for representative requests.

## Verify an event locally

Create `event.json` using the documented sample catalog:

```json
{
  "version": "2.0",
  "rawPath": "/singleband/lagoon/red/preview.png",
  "rawQueryString": "stretch_range=%5B0%2C3000%5D",
  "requestContext": { "http": { "method": "GET" } }
}
```

```sh
rcfp lambda-event -d catalog.sqlite --event event.json
```

The reply should have status 200, a PNG content type, `isBase64Encoded: true`,
and a base64 image body. A request to `/keys` returns JSON. Check both in the
actual API Gateway integration and verify binary decoding at the client.

## Validate an account deployment

Deploy through your existing infrastructure workflow. Check role credentials,
S3 range access, catalog refresh, initial and repeated invocations, timeout/error
replies, and binary image responses. Test your public frontend's CORS and basemap
configuration. If using X-Ray, set `TC_XRAY_PROFILE=true` and configure the daemon;
native subsegments require a sampled incoming trace ID. This uses native tracing
rather than Terracotta's Python SDK decorators.

Keep the previous function version and routing configuration for rollback.
Publish service-specific support only after those real-service checks pass.
