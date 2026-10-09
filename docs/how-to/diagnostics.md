# Inspect imagery and diagnose ingestion

Use the installed `rcfp inspect` command before ingestion or after moving a
catalog. It opens local SQLite catalogs read-only and never creates a catalog,
computes missing catalog statistics, updates metadata or rewrites raster paths.
Without arguments it inspects `DRIVER_PATH`, if configured, or configuration only.

```sh
rcfp inspect -r 'sample/data/{band}.tif' -o bands.sqlite
rcfp inspect -d map.sqlite
rcfp -c settings.toml inspect -d map.sqlite --json
rcfp inspect --raster sample/data/red.tif --bands 1 --render-probe
```

The pattern uses the same discovery and ordered-key rules as `ingest`.
Placeholders such as `{scene}` and `{band}` create keys in their first appearance
order. `--rgb-key band` moves that key last. Quote patterns so the shell does not
expand them. Literal braces use `{{` and `}}`. A repeated key must match the same
value, and two files cannot map to identical keys. `--bands` selects one-based
physical bands for raster/pattern inspection. It does not create logical band
bindings. Native catalog band bindings supply their own selected physical bands.

`-o` reports the absolute ingestion destination, whether it exists, and whether
its ordered keys match. It does not test writes or reserve that destination.
Ingestion requires a writable parent directory and stores raster paths, rather
than copying imagery into SQLite. `DRIVER_PATH` is the serving catalog location.
It does not replace ingestion's required `-o`. Inspect first, then run:

```sh
rcfp ingest 'sample/data/{band}.tif' -o bands.sqlite
rcfp serve -d bands.sqlite
```

The [first-map tutorial](../tutorials/first-map.md) supplies real imagery and a
browser map that works offline without an external account or basemap token.

## Raster and configuration checks

Each physical raster reports dimensions, CRS, affine transform, available and
selected bands, dtype, nodata, mask flags, color interpretation, scale/offset,
block size and overview dimensions. Size pairs are `[height, width]`.
GDAL mask flag bits are 1 for all-valid, 2 for a shared dataset mask, 4 for alpha
and 8 for nodata. These declarations do not prove that every pixel is valid.

Warnings explain full-width strips, missing overviews and overview levels that
remain larger than a 256-pixel preview. They indicate possible read costs, not a
measured latency prediction. `optimize-rasters` writes separate tiled outputs.
Register their paths after checking pixels and performance. Samples are small
enough for first use without optimization.

Configuration entries identify CLI, TOML, environment or default origin. Resource
controls also identify derived defaults such as `RASTER_CACHE_SIZE`.
`ASYNC_TIFF` and `NATIVE_DIRECT_READ` include `active`, which reflects the
compatibility profile. Numeric/boolean settings and validated rendering enums
are visible. Credential fields and other string/list settings are redacted.
Locations omit URL user information, queries and fragments. No raw GDAL,
network or TOML parsing error is included. The report can still contain local
paths and catalog keys, so share it according to your organization's policy.

## Explicit bounded probes

Default inspection makes no remote requests. Local SQLite paths and `sqlite:///`
URLs use the same provider/path resolution as ingestion. It supports local, self-contained
TIFFs and local Terracotta or native keyed SQLite catalogs. Expressions and
stored STAC source bindings are listed as skipped logical sources. Inspect their
physical assets separately and exercise their full render through the serving
API. Remote catalogs, direct STAC configurations, advanced layer catalogs,
VRTs and other formats need their deployment-specific checks.

`--render-probe` reads each selected local band into a 256×256 preview, applies
a grayscale stretch from the sampled valid range and encodes a PNG in memory.
It reports valid sample count, range, dimensions and encoded byte count. It does
not write an image. Header inspection and optional rendering run in an isolated
process with a ten-second deadline per file. Timed-out probes are terminated.
The rendered pixel count is bounded, but GDAL may decode larger source blocks
or allocate warp buffers. Inspection accepts at most 100 datasets and 256 bands per file.

```sh
rcfp inspect --raster 'https://imagery.example.com/red.tif' --probe-remote --json
```

The explicit HTTP(S) probe sends one GET with `Range: bytes=0-1023`, a five-second
timeout and no redirects. It retains at most 1024 body bytes. A 206 response with
a matching `Content-Range` demonstrates range capability. `Accept-Ranges` alone
does not. A 200 response warns that the origin did not demonstrate range support.
401/403 yields a credential/access error, without echoing response bodies or
signed query strings. Redirects and other unsuccessful statuses fail the probe.
No remote raster is opened with GDAL or rendered by this command. S3 URLs require
an HTTP(S) asset URL for this range check. Instance-role/private-object access
must be exercised inside the actual deployment.

Check the raster credential consumer when access fails. Metadata SQLite S3
snapshots use the AWS credential chain, while raster GDAL configuration uses
its own settings. Signed asset URLs may expire. Refresh them through the provider.
Avoid putting credentials into shared configuration or command transcripts.

## Output and failure contract

Text is the default, with source/destination, ordered keys, dataset checks,
configuration values/origins and actionable issues. Use `--json` for automation.
JSON is one object on stdout with `schema_version: 1` and these fields:

| Field           | Meaning                                                                                                             |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| `ok`            | No error-severity issue occurred                                                                                    |
| `configuration` | Setting name to `{value, origin}`, plus `active` where applicable                                                   |
| `source`        | Kind, redacted location, origin and catalog format when available. Otherwise null                                   |
| `destination`   | Redacted absolute location, existence, origin and raster-storage behavior. Otherwise null                           |
| `ordered_keys`  | Key names in catalog/ingestion order                                                                                |
| `datasets`      | Keys, source kind, physical path, selected bands, inspection status, raster declarations and optional probe results |
| `issues`        | `{severity, code, message, next_step, dataset}`. Dataset is a zero-based report index or null                       |
| `limits`        | Dataset/band, time, output-pixel and remote-body bounds                                                             |

Optional or unavailable checks use null probe fields or `inspection: "skipped"`.
Failed physical checks use `inspection: "failed"`. A configuration parse failure
also emits this JSON envelope, with empty configuration and a configuration issue.
Consumers must tolerate additional fields and issue codes within schema version 1.
Text wording is descriptive. Scripts should use JSON fields and exit status.

Exit 0 means there are no errors. Warnings and skipped checks can still occur.
Exit 2 means invalid arguments/configuration, a failed destination/catalog/band/
raster check, denied credentials or an unsuccessful requested probe. Common
pattern failures point to quoting, placeholders, existing files and unique keys.
Path failures point to mounts and stored absolute paths. Do not equate an exit 0
with qualification of skipped remote or logical renders.

## Diagnose in the deployment environment

Run inspection with the same configuration and mounts as serving:

```sh
rcfp -c settings.toml inspect -d /data/catalog.sqlite --render-probe --json
rcfp -c settings.toml serve -d /data/catalog.sqlite --bind 0.0.0.0:5000
```

For [containers](container.md), execute `inspect` inside the image with the same
`/data` mount and environment as the service. For [Python/WSGI](wsgi.md), run the
CLI in the installed Python environment with the same TOML/environment settings
before constructing the application. Python freezes profile selection at import.
For [Lambda](lambda.md) or a [cloud test host](cloud-test.md), inspect the packaged
catalog and local sample paths, then verify private remote raster access through
that host's role/provider and serving API. Local probes do not qualify IAM,
network routing, runtime images or remote performance.
