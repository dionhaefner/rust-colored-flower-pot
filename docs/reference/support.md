# Compatibility and support

RCFP has two process profiles: default `native` and the pinned
`terracotta-0.10.1` compatibility contract. The package and primary import are
`rcfp`. HTTP protocol identity remains 0.10.1. Terracotta catalogs use format 0.10 and
native layer catalogs use `rcfp-native-1`.
The package version in this site's title is supplied by Hatch VCS metadata,
not by either compatibility identity.

## Interface coverage

| Surface                                               | Native                                                | Terracotta profile                                                      |
| ----------------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------- |
| Discovery, metadata, singleband/RGB/compute, palettes | Shared HTTP core                                      | Legacy public HTTP behavior                                             |
| Python catalog and requests                           | DataStore, NativeApplication, byte-oriented rendering | Native APIs plus documented settings, drivers and masked arrays/futures |
| WSGI                                                  | Small native Application/create_app                   | Documented server.app:app over the native WSGI adapter                  |
| CLI                                                   | Native core commands plus structured/Lambda commands  | Supported Terracotta commands and flags implemented in Rust             |
| Catalogs                                              | Compatible SQLite/MySQL/PostgreSQL encoding           | Same supported legacy format and write fences                           |
| Remote SQLite on S3                                   | Read-only snapshot                                    | Read-only snapshot with connection-generation semantics                 |
| UTM sampler                                           | Explicit opt-in. Pixels may differ                    | Disabled to preserve profile rendering semantics                        |
| Web Mercator fast reads/prefetch                      | Explicit opt-in                                       | Compatible opt-in paths with fallback                                   |

Catalog format checks reject unsupported markers before writes. Older schemas
need explicit migration. The compatibility profile is a public-workflow contract,
not a guarantee for private backend internals. See [migration](../how-to/upgrade-terracotta.md)
for exclusions, verification, and rollback.

## Platform and deployment evidence

| Target                                            | Requirements and validation scope                                                                                                                            |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| CPython                                           | 3.12–3.14 on the wheel's matching OS/architecture. Limited-API `abi3` extension                                                                              |
| Linux x86_64                                      | Local installed-wheel, real-raster, Python/WSGI, HTTP, and compatibility checks. Release glibc requirements come from the repaired wheel tag                 |
| Linux aarch64, macOS x86_64/arm64, Windows x86_64 | Configured wheel/build and artifact-test targets. A configured lane alone is not a recorded passing run for your release                                     |
| Native container                                  | Linux bundle with compatible container libc/native libraries. Catalog paths must refer to container-accessible rasters                                       |
| Lambda                                            | Custom AL2023 runtime bundle, matching architecture, API Gateway v1/v2 adapters. Event/local runtime-image checks are separate from an actual AWS deployment |
| SQL providers                                     | Provider contract tests and verified TLS configuration. Qualify the actual engine/certificates used by your deployment                                       |
| S3, role/SSO/custom endpoint, X-Ray               | Require real-service/credential-path qualification in the intended account. Local emulators do not establish that support                                    |
| Browser                                           | Raster/client checks are separate from external basemap/token integration                                                                                    |

Select artifacts using their release validation results. Do not infer a passing
macOS/Windows/cloud deployment from a Linux docs build. The included examples
exercise local data without external account requirements.

Native `rcfp-native-2` [keyed catalogs](layers.md) currently write local SQLite
and support read-only remote SQLite snapshots. The `rcfp-native-1` advanced
layer format remains local SQLite.
Their metadata, definitions, Python, CLI and HTTP rendering share the native core.
Existing Terracotta-format SQLite/MySQL/PostgreSQL/remote workflows retain their
separate support contract. The bundled viewer uses the public
[JavaScript SDK](javascript.md) for key-based maps and native default layer images.
