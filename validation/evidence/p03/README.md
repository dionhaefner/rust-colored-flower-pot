# P03 validation evidence

P03 was validated on 2026-09-22 against frozen reference commit
`6db38d5873cde5ebabe47338a1f61ddc788eada6`, its pinned Python 3.12.3
environment and GDAL 3.9.3, and the checksum-pinned real raster corpus.

## Relocatable bundle, Python, WSGI, and Lambda adapter

```bash
packaging/build-bundle.sh validation/artifacts/p03-bundle
packaging/build-python.sh \
  validation/artifacts/p03-bundle validation/artifacts/p03-python
packaging/build-lambda-target.sh validation/artifacts/p03-lambda-target
TERRACOTTA_LAMBDA_TARGET=validation/artifacts/p03-lambda-target \
  packaging/build-lambda.sh \
  validation/artifacts/p03-bundle validation/artifacts/p03-lambda
.venv-reference/bin/python validation/tools/p03_validate.py \
  --out validation/artifacts/p03-local
```

The final local report passed 21/21 checks. It imports the CPython extension
without reference-venv loader variables; verifies PNG bytes are returned and a
missing dataset raises `TerracottaError`; runs the WSGI application; covers API
Gateway v1 JSON and v2 binary/base64 responses; and drives the zip's executable
bootstrap against a loopback fake Lambda Runtime API. Python extension, WSGI,
direct Lambda event, and packaged-runtime PNGs decode exactly like the frozen
real-raster reference. The retained `local/side-by-side.png` was inspected at
native scale and shows no visible difference.

The Python-created source database renders through Rust. Rust then writes one
record using the legacy SQLite schema/float32 encoding; frozen Python reads the
same ordered keys, dataset, bounds, 99 percentiles, and extra metadata, after
which Rust renders it exactly. This is the P03 format feasibility operation,
not the complete P05 write API.

The relocatable host bundle is 66,761,789 bytes. The final Lambda zip is
26,055,524 bytes with SHA-256
`ab12fd06b877f0a6243c2886059176c5a3fcd729b7b6a71127be29b088efc4ae`.
See `local/report.json`.

## Container

```bash
.venv-reference/bin/python validation/tools/p03_container.py \
  --out validation/artifacts/p03-container
```

The clean `debian:bookworm-slim` Linux/amd64 image built successfully. Its
runtime network was disabled; the bundled executable opened SQLite and rendered
the pinned real Sentinel-2 subset with exact decoded RGBA. The retained image is
`terracotta-p03-local:20260922-2`, image ID
`sha256:5927ca5051d93c1d7b9927827d51ea991f66702a9b3da4bcd5dc23e8a456516e`,
156,313,238 bytes. See `container/report.json` and `container/container.png`.

## Lambda target ABI and native package

```bash
.venv-reference/bin/python validation/tools/p03_lambda_runtime.py \
  --out validation/artifacts/p03-lambda-runtime
```

The first host-built experiment correctly failed in `provided.al2023`: it
required glibc 2.35 while the runtime exposed 2.34. The final binary is built
inside the pinned official image
`public.ecr.aws/lambda/provided@sha256:6e44223227dfb00686065ab66db7c4ce86f1483a8f69e40bcba5ebdb0f4d5fd7`,
requires at most glibc 2.34, and bundles the needed AL2023 expat, libgcc,
libstdc++, and zlib runtimes and notices. With network disabled it loaded the
complete GDAL runtime and rendered exact decoded RGBA. See `lambda/report.json`
and `lambda/lambda-al2023.png`.

This clears local event, Runtime API protocol, artifact loading, target ABI, and
native-dependency evidence. It does **not** claim an AWS Lambda service
invocation; no cloud deployment or spend was authorized. The exact resume step
is to create a disposable x86_64 `provided.al2023` function from the retained
zip, point `TC_DRIVER_PATH` at an authorized reachable database/raster, invoke a
`/keys` event and the retained image event, compare the decoded response, then
delete the function and record region/config/log evidence.

## Regressions and platform matrix

- `validation/tools/check.sh`: seven cached corpus parents and all subsets
  verified offline, 19 comparator mutation tests passed, 14 Rust tests passed.
- `cargo clippy --locked --offline --all-targets -- -D warnings`: passed.
- `cargo build --locked --offline`: passed.
- P01 regression: 26/26 exact HTTP/JSON/decoded-RGBA cases passed; see
  `p01-regression/report.json`.
- P02 regression: 8/8 exact RGB cases, local/range equivalence, cache and three
  concurrent reads passed; peak sampled RSS 193,363,968 bytes under 512 MiB;
  see `p02-regression/report.json`.

| Target | Result | Scope |
| --- | --- | --- |
| Linux x86_64, glibc 2.39, CPython 3.12 | Passed | Native bundle, extension, WSGI, local Lambda protocol |
| Debian bookworm Linux/amd64 container | Passed | Clean image, SQLite + real raster, offline runtime |
| Lambda `provided.al2023` x86_64 image | Passed locally | ABI, native dependencies, SQLite + real raster |
| AWS Lambda service | Not run | Requires explicit deployment/spend authorization and reachable data |
| Linux arm64 / Lambda arm64 | Not run | Separate target build and qualification required |
| macOS / Windows | Not run | P07/P08 platform work |

No active process or container remains. The passing Docker image is retained;
all bulky build/package outputs remain reproducible under ignored
`validation/artifacts/`.
