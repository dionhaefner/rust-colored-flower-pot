# Validation handoff

The [application validation plan](adoption.md#executed-repository-evidence)
records fresh automated migration, upgrade/restore, mosaic, scientific-grid,
first-use and corpus qualification from 2026-10-07. Independent application
integration, production cost/latency, continued usage and consented case studies
are post-release follow-up observations to collect as applications adopt RCFP.
They remain unqualified and are not release acceptance criteria.

This document records completed local checks, raster correctness results,
and the remaining platform, service, release, and conditional support
qualification. Earlier local evidence was collected on **2026-10-05**, with the additional
optimization qualification below recorded on **2026-10-06**. Results establish
only the recorded source and artifacts. Rerun affected checks after changes.
Configured CI, a completed execution, and a passing qualification are separate
statuses. The corrected broader raster matrix passes **all 3,646 cases** on the
recorded Linux x86_64 shared core. Hosted platform qualification remains pending.

Run commands from the repository root against the reviewed revision and exact
artifacts. Keep fixture/reference acquisition and reports in ignored `build/` or
`.cache/`, use fresh output directories, and retain hashes, environment, commands,
failures, exclusions, skips, and unavailable checks separately. Never regenerate
reference golden outputs from RCFP results. Publishing, deployment, and cloud
spending require session authorization.

## Completed local checks

The fixed Linux x86_64 native bundle and repaired wheel passed the following
checks. The wheel was installed on CPython 3.12, 3.13, and 3.14:

- [x] Rust formatting, warnings-as-errors Clippy, release build, 28 unit tests,
      and all 18 native profile/admission cases.
- [x] All 53 native and 15 compatibility installed-wheel cases on each Python
      version, plus 26 exact decoded-image HTTP comparisons in each profile and 28
      selected public reference replay cases.
- [x] Render admission corrected to configured worker-plus-queue capacity.
      180 seconds with eight clients completed 208,500 requests without overloads
      or timeouts. The admission regression detects the original failure.
- [x] SQL driver caching separates changed credentials and TLS options. Both
      live PostgreSQL 18/MariaDB 11.8 providers reject the changed-CA reproduction,
      pass all 16 fresh-process TLS cases, and pass reference round trips and
      warmed-reader rejection checks without invalidating the original reader.
- [x] Wheel/sdist core metadata 2.4, pinned Twine 6.2.0 strict validation,
      contents/RECORD/boundaries, and git-free development Hatch VCS version
      round trip. The source rebuild used a prepared native bundle.
- [x] Both build-failure diagnostics, installed-wheel reference extraction,
      Python lint/formatting, and product boundary scan.

Earlier local artifacts also passed Rust SQL/remote integration, 46 catalog
assertions per profile on SQLite/MariaDB/PostgreSQL, HTTP raster ranges on all
three Python versions, real-raster native/Lambda-event and wheel smoke checks,
editable installation, and checksum-pinned GDAL runtime provenance. Chromium
selection/singleband/RGB/metadata/error checks used an offline basemap. Physical
BigTIFF checks verified 5,041 blocks/masks and actual HTTP ranges beyond 4 GiB.
Strict local Sphinx HTML, links, examples, migration rehearsal, and
browser/search/mobile checks passed. These earlier results qualify their own
artifacts and were not rerun on every later core.

Rust and Python coverage were recorded separately, with no thresholds selected.
The matched-cache warm-preview control completed three trials at concurrency
one/two with exact imagery. It does not establish cold/WAN or production gains.

The pinned oracle baseline had 526 passes and two classified ANSI failures out
of 528 cases, with six remote-SQLite mock cases excluded. These are reference
classifications, not RCFP product passes. The 28-case public replay selects
supported workflows. Live SQL checks have a separate scope.

## Raster correctness

The corrected matrix passes **3,646 cases, zero failures**, with no skips or
unexpected oracle errors, on Linux x86_64. Each profile passes 1,823 cases.
The exact shared core, source identities, GDAL runtime, oracle, fixture recipes,
per-case arrays and decoded images are retained in
`build/raster-fixes-20261005/matrix-final/`. The matrix also passes all 3,646 cases
against the core installed from the repaired wheel, with that wheel's GDAL runtime,
in `matrix-wheel/`. Shared-core SHA-256:
`2289447cebc0307dda1980017556183a26777f78fecaa125dca3fbb5b4a96def`.
Source identities stayed unchanged throughout the run. The previous 358-failure reports remain separate evidence.

| Check group                                              | Passed | Failed |
| -------------------------------------------------------- | -----: | -----: |
| Full/sample/chunk metadata                               |    102 |      0 |
| Raw values, masks, dtype/shape and sparse-tile rejection |  3,424 |      0 |
| HTTP status and decoded images                           |    120 |      0 |

Seventeen inputs cover pinned real polar and adjacent dateline DEMs, a
native-column mosaic across 180°, rotated opaque/masked derivatives, dataset
masks/RGBA, UInt8/UInt16/Float32 data, nearest/average overviews, and
EPSG:3413/3035/3857 reprojections. Both profiles run all 16 supported warp/read
pairs on previews, overview scales, detail windows, source edges and
value-preserving reads, plus meridian tiles and singleband/compute/RGB/partial-RGB
images. Deliberate native hull and transparent-image size differences use
independent expectations. Native direct reads, opt-in fast samplers and prefetch
are explicitly disabled in this general-reader matrix.

The general reader suggests resolution from an axis-aligned proxy of the
source bounding rectangle, matching the reference's bounds-based calculation
for rotated grids. Its padded VRT receives the affine directly through
`GDALCreateWarpedVRT`. Reconstructing resolution from padded bounds through the
warp utility introduced small sampling differences. Read resampling writes the
source dtype before widening the transfer buffer, dimensions use ties-to-even
rounding, and the output mask checks source nodata even when destination nodata
has been removed for alpha. Alpha reads also retain their sample precision.
No raw-value or image tolerance was added.

The three flagged examples were independently reproduced against the original
frozen core and retained oracle arrays. The corrected core has zero differing
masks and values in all three: rotated linear/average preview (previously 29 mask
pixels and 1,179 common-valid values), Web Mercator linear/average edge
(previously 271 UInt16 values), and DEM average/nearest preview (previously one
Float32 value). See `regression-original-vs-fixed.json` in the same evidence root.

Fresh formatting, warnings-as-errors Clippy, all 35 Rust unit tests, all 18 native
profile/admission cases, and both 26-case decoded-image HTTP comparisons pass.
The repaired Linux x86_64 wheel passes 53 native and 15 compatibility cases on
each of CPython 3.12, 3.13 and 3.14, plus the 28-case selected public replay.
Wheel contents/RECORD/metadata and strict repair checks pass. Wheel SHA-256:
`260360f56a0a953c77349eb152290fd662b4c3661c19bca04bc1a08b8027e8d0`.
Two socket tests were initially blocked by the sandbox. The complete unit suite
passed with loopback access. Earlier platform/resource/service results above
qualify their own artifacts and are not reruns of those scopes on this core.

These checks need no human account access. Retain an existing verified
`.cache/reference` and `.venv-reference`. Otherwise prepare CPython 3.12 and the
pinned oracle with `tests/reference/bootstrap.sh`. Configure
`TERRACOTTA_GDAL_LIBRARY`, `GDAL_DATA`, and `PROJ_DATA` for the supported native
runtime, then run:

```sh
packaging/cargo.sh build --locked --release
.venv-reference/bin/python tests/reference/raster_boundaries.py \
  --library target/release/librcfp_core.so \
  --reference-python .venv-reference/bin/python \
  --out build/raster-review --download
```

This is the Linux shared-library example. `--download` acquires missing
checksum-pinned sources. Omit it when the cache is complete. The default oracle
archive is `.cache/reference/source.tar.gz`. This local run used
`--reference-archive build/reference-acquisition/source.tar.gz`.
[Reference procedures](../tests/reference/README.md#broader-raster-correctness)
describe the comparison rules. The correctness workflow includes this
matrix and retains its reports. That configured lane has not been executed on
the hosted reviewed revision. Other OS/architecture wheel cells and opt-in
samplers still need their separate qualification.

## Other pending qualification

The following scopes still need fresh evidence. Account/platform work requires
the intended environment. Repository test extensions can be performed locally.
Performance measurements are needed before expanding performance claims, and
cross-endian work is conditional on proposing that support extension.

| Remaining scope                           | How to execute and what to retain                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hosted platforms and correctness          | Make the reviewed tree available on an authorized branch. Run `gh workflow run platforms.yml --ref BRANCH`, `wheels.yml`, `correctness.yml`, and `docs.yml` the same way. Inspect Linux x86_64/aarch64, macOS x86_64/arm64, and Windows x86_64 native targets and all 15 CPython 3.12–3.14 wheel cells. Verify Windows Cargo artifacts before installing the Python Rustup helper, strict Linux glibc/ISA repair tags, coverage, reference baseline, browser, physical BigTIFF ranges beyond 4 GiB and the full 180-second resource job. Review separate Rust/Python coverage before choosing thresholds. The correctness workflow includes the full raster boundary matrix. Inspect its execution and retained report alongside the other gates. Download artifacts with `gh run download RUN_ID --dir build/hosted-RUN_ID`. Retain revision and artifact hashes. A dispatch of an older branch does not test this working tree. |
| Intended SQL TLS                          | Configure disposable MySQL/PostgreSQL servers with the intended private/public CA and server identity. Set `RCFP_TEST_MYSQL_URL` and `RCFP_TEST_POSTGRESQL_URL` with `sslmode=verify-full` and `sslrootcert` where needed. Run the Rust storage integration and native catalog suite. On both native and Python paths, repeat valid CA, missing/untrusted CA, wrong hostname, and literal-loopback `sslmode=disable`, in fresh processes and with warmed driver reuse. Confirm negotiated TLS server-side. Drop the test databases. Local MariaDB is not a hosted MySQL result.                                                                                                                                                                                                                                                                                                                                                   |
| Real S3 and credentials                   | In an authorized test prefix, use a pinned real COG and a consistent SQLite backup. Follow [remote-data procedures](../docs/how-to/remote-data.md). Confirm raster range reads, read-only catalog writes failing, generation replacement with an old outer Python reader pinned, and failed-refresh recovery. Repeat with the intended custom endpoint, session token, role refresh and SSO renewal. Retain redacted request/signing/refresh evidence and delete test objects.                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Lambda, both architectures                | Run the native platform workflow's x86_64/aarch64 AL2023 image/build/runtime lane. The local qualification environment had no Docker/Podman. Then deploy those exact bundles through the authorized infrastructure workflow, as described in [Lambda procedures](../docs/how-to/lambda.md). Invoke `/keys` and image events through the actual API Gateway integration. Decode PNG/base64, repeat warm/cold/error invocations, check shutdown and credentials, and confirm a sampled trace arrives in X-Ray. Retain hashes, configuration and results, then remove disposable functions/roles/objects.                                                                                                                                                                                                                                                                                                                            |
| External basemap/deployment               | Start the bundled client against the intended backend and real basemap/token. Select a dataset. Exercise singleband, RGB, metadata, missing-data/error handling, navigation and CORS. Retain screenshots, decoded raster results and browser/network errors. The local Chromium run used an offline basemap.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Read the Docs and human trial             | Connect the repository in the owner account, build the reviewed revision and release versions using `.readthedocs.yaml`, activate versions, and set the actual canonical URL. Retain hosted build logs and verify navigation/search/mobile pages. Have an independent user follow installation/first-map and Terracotta migration/backup/rollback instructions and record issues. Local Sphinx, examples, browser and external links have already run.                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Exact release and publication setup       | After the raster matrix and required gates pass, build at the intended `vMAJOR.MINOR.PATCH` tag. Check all five wheels and the sdist with `python packaging/verify-release.py DIST vMAJOR.MINOR.PATCH`, plus strict Twine checks. Configure protected main/tags, the `pypi` environment and PyPI trusted publisher. The tag-push workflow publishes, so do not push a release tag until publication is authorized. Local development versions do not qualify the tag.                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Opt-in sampler correctness                | Add explicit opt-in variants to `tests/reference/raster_boundaries.py`. Its current matrix disables fast samplers and prefetch. Exercise `TC_RUST_MERCATOR_FAST`, native `TC_RUST_UTM_FAST`, decoded-block caching and prefetch on eligible inputs plus fallback edges. Compare exact-path values/masks/images and use independent coverage, category and seam checks for intentional native sampling differences. Apply the input-specific UTM bounds in [compatibility notes](compatibility-notes.md#narrow-accepted-numerical-differences) only to the recorded EPSG:32632 zoom-13 UInt16 corpus. Broader inputs need fresh evidence. Verify that compatibility mode disables UTM sampling even when requested. Retain exact core/wheel identities and per-case results before expanding support claims.                                                                                                                       |
| Cold/WAN/performance and longer resources | The local matched-cache warm benchmark and 180-second resource gate are complete. Before expanding claims, use disposable benchmark hosts with matched total cache budgets and CPU affinity to measure actual cold disk, WAN bytes, startup/first view, loaded p95/p99, production budgets, more cores and a longer soak. Run `python docs/tools/benchmark.py --reference-python .venv-reference/bin/python --out build/warm-review` for the maintained warm control. Preserve exact binaries, input hashes, workload and exclusions. Do not reuse its warm ratios as WAN/production claims.                                                                                                                                                                                                                                                                                                                                      |
| Cross-endian support extension            | If cross-endian support is proposed, use an actual big-endian target or emulator with native dependencies. The endian-tagged format proposal was discarded. Existing cross-endian catalogs remain unqualified. Create/read legacy float32 percentile blobs in each byte order and compare known stored percentiles and rendering in both directions. Record unsupported combinations explicitly. Byte-swapping on this little-endian host alone does not qualify cross-endian operation. A format change requires a separate decision.                                                                                                                                                                                                                                                                                                                                                                                            |

## Retained scope decisions

Preserve the current catalog layout and image mask/transparency contracts.
Endian-tagged percentile encoding and a broad transparency redesign were
discarded. A future portable format needs an explicit marker and migration.
Legacy cross-endian catalogs remain unqualified. Native percentile label and
rotated metadata-hull corrections change behavior within the existing format.
Public differences are described in [native migration](../docs/native-migration.md).

Speculative expression rewrites and cross-file transformer sharing are not
outstanding implementation tasks. Worker-local affine plan reuse has fresh
measurement and qualification below. Retain bounded caching/prefetch
and the GDAL fallback. Reconsider optimizations with fresh measurements and a
concrete requirement. Broader hosted raster qualification and opt-in sampler qualification
remain the explicit tasks above.

## Evidence retention and completion

| Retained local directory                     | Scope                                                                                                                                                                                                                                                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `build/validation-20261005/`                 | Earlier Linux artifacts: SQL/storage, remote ranges, reference baseline, browser, physical BigTIFF, documentation, coverage and warm control. Original failures and setup corrections remain separate.                                                                                            |
| `build/blocker-fixes-20261005/`              | Fixed native bundle/repaired wheel: admission, SQL cache/TLS, metadata/build gates, contracts and reference replay. Native SHA-256 `7dd4e8c88e400471c6c14bcc7990599702e0acf63b3b932733abc655d672c805`. Repaired-wheel SHA-256 `c56620feb6287a1399d72a0c5c81e96bb83f5638aaaa2e0c74be4104b6f395f4`. |
| `build/raster-correctness-20261005-current/` | Original frozen core and the full matrix with 358 failures, retained as negative evidence.                                                                                                                                                                                                        |
| `build/raster-fixes-20261005/`               | Corrected frozen core, full passing matrix, original-vs-fixed reproductions, rebuilt wheel and fresh interface/unit checks. Initial sandbox and wheel-tool setup failures are retained separately.                                                                                                |

Each directory has a README and machine-readable reports with exact identities.
Copy evidence before cache/build cleanup if it must survive. If it is unavailable,
rerun the maintained checks rather than presenting the summary here as fresh
release evidence. Prepared CI is not execution evidence, and development-version
source rebuilds do not qualify exact release tags or fresh native source builds.

Close a pending item only after its whole stated scope passes on the reviewed
revision and exact artifacts. Retain commands, inputs/hashes, environment,
results, classified exclusions, and cleanup records. Any accepted numerical
difference needs an explained input-specific bound and an independent check of
masks, coverage, categories, seams and placement.

## Native navigation performance evidence

`build/navigation-performance/` retains frozen baseline/staged/final executables,
full checksum-pinned raster originals/derivatives, sequential paired reports,
preparation costs, independent native quality checks and failed setup runs.
See [performance](performance.md) for each ranked optimization's implemented/discarded
disposition, measured local/range gains, actual S3 status and defaults.

Native direct tile sampling is enabled by default. The independent 193-check
matrix passed each of nearest/bilinear/cubic/average, including real polar/
dateline/rotated/unaligned-Mercator/northern/southern sources, nodata/alpha, overview/edge/rectangular
outputs, categories, multiband RGB and explicit WebP coverage. Both profiles'
standalone contracts and seven local cache-freshness checks passed. The new path
completed the full 180-second eight-client/three-worker resource test with
152815 successful HTTP requests and no failures/overloads/timeouts. This is
uncached-render stress. It is not a production memory claim for a large resident
cache. Unit and Clippy checks passed separately.

These tile gates do not close the broader metadata/preview correctness matrix,
fresh release/wheel/platform/browser/cold-storage gates, private provider TLS or
credential/mutable-object qualification above. Earlier frozen failed matrix
results describe their original artifact. Native exact Terracotta pixel controls
must disable direct sampling. The direct path has a separate native quality
contract. Keep failed S3 and contaminated/incomplete timing runs separate from
successful paired measurements, as described in the performance document.

The local installed wheel passed 14 adapter/CLI/WSGI contracts and a nonempty
native RGB/defaults/format check. Sphinx and 4159 local links passed. Raw-wheel
verification and portable manylinux_2_34 repair failed. This host requires
manylinux_2_39 symbols. This is a portability limitation, not a local API pass
or a release-qualified artifact. That earlier wheel did not run a fresh Terracotta differential replay.

## Cross-service navigation comparison

Three fresh-process rotating-order trials against Terracotta 0.10.1 and
TiTiler 2.4.0 / rio-tiler 9.4.9 passed all 216 case runs and 45,360 tile
requests, with GDAL 3.10.3, four physical cores and equal 160 MiB aggregate
cache budgets. The native executable hash is
`4624c1e46b84613b6d496ed9a928685bab62d3a696631f64e89898dbec8f7ed3`.
The focused source set includes full Sentinel, DEM and WorldCover singleband
layers plus separate-source RGB, SQLite, local/range storage, one/three users,
pan/zoom/revisit, stretch and ratio-expression changes. Response completion,
decode, startup, source traffic, tails and process-tree PSS/USS are separate.
Every native case ended with zero in-flight work and matched submitted/completed
counts. This successful-request check does not test cancellation during GDAL.

All 252 independent native saved-tile cases matched direct GDAL samples/masks
exactly. Terracotta/TiTiler samples differ under their grid/overview/rounding
policies, including some category/coverage edges, and remain separately reported.
All local/range decoded images matched within each service. Executed evidence
is in ignored `build/service-benchmark-default-concurrency/` and
`build/service-benchmark-quality-default-concurrency/`. TiTiler's default
request threadpool is retained and all four health PIDs are ready before timing.
The earlier bounded-threadpool comparison, setup, smoke and interrupted
preliminary timing runs are excluded from the root README headline figures.
Methods, exclusions and pinned environment locks are maintained in `tests/`.

Issue #8 retains the completed controlled comparison and requires only a
representative real deployment with the intended client network and S3 storage.
Record actual viewport delivery, errors and resources with exact deployed
artifact/input identities. Retain the controlled comparison's applicability
limits rather than inferring hosted speedups. Physically cold disk, detailed
browser profiling, true time-series acquisitions and expanded packed multiband/
mosaic matrices are outside this issue's completion scope. Release/platform and
provider qualification above remain separate. Deadline/disconnect behavior belongs
to `tests/request_lifecycle.py` as a correctness gate. It is not an additional
comparative benchmark requirement. The measured RCFP profile is native. No fresh
exact Terracotta-profile differential replay is implied by these timings.

## Request lifecycle correctness

A local Linux x86_64 run of `tests/request_lifecycle.py` passes all six cases
across native and Terracotta profiles for executable SHA-256
`db6c9a4cc4d53d31b8fe5a9ec90efd9f39dd07ca59b4343fec6baabf143276b6`.
It verifies active accounting after deadlines/disconnects, queued expiry without
source access, bounded counted rejections, drain and subsequent successful renders.
Evidence is in `build/request-lifecycle-20261006/run-3/report.json`. Earlier
sandbox and harness setup failures remain separate. The controlled range origin
serves checksum-pinned real raster bytes. This is a correctness pass for that
artifact, with native SIGTERM cleanup. Graceful capsule joining, hosted platforms
and real deployment evidence retain their separate qualification boundaries.

## Additional native optimization qualification

The initial cross-service and resource-test executable has SHA-256
`ecfdc05bbe70aa46354ee996d97c71dfb2a1a8d07845330b2294f023a29a1c8c`.
Evidence is in ignored `build/aggressive-performance-20261006/` and describes
this development artifact, not a portable release:

- Release build, formatting, all-target warnings-as-errors Clippy and 41 Rust
  unit tests pass. The explicit image timing test is ignored in the ordinary
  test run. Its separately executed encoder experiment is recorded in
  [performance](performance.md).
- Each of bilinear/nearest/cubic/average passes 241 independent native checks,
  **964 total**, adding real shared external masks, a nodata-free four-band
  derivative and external overviews to the prior geographic/alpha/edge matrix.
  Coverage, category colors, dimensions and grids are checked separately from
  the continuous one-display-level bound. No new numerical tolerance is added.
- All 18 standalone cases across both profiles and all seven local cache
  freshness checks pass. Unit tests cover source defaults/explicit zero,
  zero-budget active-render sharing, capacity accounting, compact sample bits,
  hot-tier eviction/stale promotion, and AVX2/scalar equality on ties and unusual
  IEEE inputs.
- The 180-second, eight-client/four-grid resource check completes **340,894 HTTP
  requests** without failures/overloads/timeouts. Three workers complete
  173,906 admitted jobs and share 166,993 active renders. No array/finished-image
  retention is enabled. Peak RSS is 81.4 MiB, descriptors 6 → peak 18 → 9.
  Correctness/resource checks overlapped during part of the soak. This is a
  bounded-resource pass, not a sustained-throughput benchmark.
- The locally built `cp312-abi3-linux_x86_64` wheel
  `0.1.1.dev10+g017bcf8f3.d20261005`, SHA-256
  `218a0d9a094e5df14b79ef99731b9d54563ce9dfe24ccfe12bedd1ef6cc3c2e8`, passes all
  15 installed Python/CLI/WSGI cases, including the pinned Terracotta public
  Python/CLI/HTTP/storage roundtrip. This is a focused replay, not the complete
  older 28-case gate or a fresh portable wheel qualification. Reference docs
  were regenerated from this installed wheel, preserving Hatch VCS versioning.

The discarded Byte-mask experiment also passed its correctness gate. Its mixed
navigation regressions are negative performance evidence. Earlier sandbox socket
failures, initial Clippy/style failures and wheel-tool invocation errors are
retained separately from corrected passing executions. Kernel `perf` counters
were unavailable under host policy. Stage instrumentation was executed instead.
Fresh hosted OS/ISA artifacts, non-AVX2 performance, actual S3/provider credential
rotation, mutable-URL semantics, browser/WAN, cold disk, the full broader raster
matrix and portable release qualification remain unexecuted for this artifact.

The final source adds a conservative two-axis eligibility check. Compound or
three-axis sources retain GDAL utility handling of vertical values/units. Final
native SHA-256 `e62c7b6bc8a54c91e57c3674ae9da2099c064a77e1ea080515432cdc3cbf69a3`
passes the release build, 41 unit tests, all-target Clippy, 18 profile contracts,
seven cache-freshness checks and **249 independent rendering checks per policy,
996 total**. A real DEM derivative with EPSG:4979 and foot band units exercises
this fallback. The full `guarded-pair` repeats all 36 navigation cells in three
alternating trials against the original baseline, with exact decoded images.
Median pan gains are 1.49× local and 2.76× range, first views about 1.2×.

The earlier cross-service artifact passes all **45,360 requests / 216 cases**
against Terracotta/TiTiler, all 252 native independent GDAL saved-tile references,
and separately recorded local/range image equality. Its hash is explicitly
recorded in the public performance explanation and differs from the guarded
artifact. All timed sources are two-axis. The additional guard is exercised by
the independent rendering matrix. Public S3/cold-disk/browser/WAN claims are not
inferred from the controlled origin.

The guarded local wheel, SHA-256 `b433fdabe233c0bbc3e002f148c9b1939fbad962d15a63b78ed8c46ac474343a`, passes all
15 installed Python/CLI/WSGI and focused pinned-reference cases. Its reference
snapshots were regenerated from the installed guarded core. Strict Sphinx HTML
and all 4,162 local links pass. Both local wheels retain their raw Linux tag.
Portable OS/ISA/release artifacts remain unqualified. The resource soak above
applies to its explicitly recorded pre-guard binary, not a repeated final soak.

## STAC selection and shared catalog serving qualification

The installed Linux x86_64 / CPython 3.12 development wheel has SHA-256
`6c31b7746596961bc4b86184eb7242cc3b5cb1a7a8d7d43ac38311605eba7f0b`.
Its frozen native executable has SHA-256
`0f85422c336ad55b90920566071a892a17aded384ddf5556d090e7310cafade0`.
Ignored `build/stac-source-validation/` retains artifact/source identities,
commands, reports, browser screenshots and earlier failures separately.
This is a raw local Linux wheel, not a portable or release-qualified artifact.

- All **30 CLI/HTTP/Chromium serving cases** pass against the final installed
  wheel. Automatic `--source` discovery covers Item/ItemCollection, nested static
  Catalog/Collection, remote documents and SQL catalogs, including SQLite files
  named with a JSON extension. Local source configurations resolve relative
  source, credential, revision and signing files. Empty and filtered POST
  searches paginate completely. Source limits and malformed/cyclic configurations
  fail explicitly. The regular Python application, one-off helpers, WSGI and
  Lambda-event render the same pinned observation through the shared routes.
- Shared discovery/filtering/paging, metadata and batch metadata, automatic TIFF
  asset discovery and optional allowlists, singleband, RGB and compute,
  request-time bands/ranges, measured default stretch, scale/offset (including
  negative scales), collection-less and encoded keys, immutable staging,
  credential refresh, budgets, failures, methods and CORS pass. Independent GDAL
  utility reads verify XYZ samples and coverage from pinned Sentinel crops and
  packed real bands. Preview bytes match SQL rendering through the public
  CLI/HTTP interface. Different calibrations of one checksum share raw reads
  without changing retained samples. Chromium uses `serve --source` and the
  standard viewer controls for asset switching, zoom, display inspection, RGB
  and revisioned share restoration without external requests.
- All **25 installed Python STAC contracts** pass, including GET/POST/merge
  pagination, deterministic selections, duplicate/conflict handling, bounds,
  credential/signature refresh, private-field isolation, real multiband samples,
  verified revisions, nodata validation and nested-VRT rejection.
- All **53 existing installed catalog contracts**, **15 compatibility cases**
  (including the pinned Terracotta roundtrip), **51 native layer cases** and
  **37 existing Chromium viewer checks** pass against the same final wheel and
  executable. This does not replay every broader raster/platform/provider trial.
- Release build, warnings-as-errors **production Clippy**, scoped Rustfmt, Ruff,
  Prettier, **11 SDK runtime cases** and strict TypeScript declarations pass.
  The full offline Rust suite passes **52 unit tests**, with two TIFF fixture
  matrix tests and one image timing test explicitly ignored. Their separately
  prepared inputs are not supplied by this qualification.
- Installed reference snapshot checking, strict Sphinx HTML and **4924 local
  links across 52 pages** pass. Documented CLI/HTTP/WSGI/Python examples and two-way
  Terracotta migration rehearsals execute against the final wheel. Wheel/source
  archive contents and Hatch VCS metadata checks pass.
- The installed STAC suite records five indexed selection trials each for
  **1000, 100,000 and 1,000,000 provider rows**, returning the same three
  observations in one page without raster reads. Query plans use the covering
  time index. Selection timings and real-raster first/repeated render timings
  remain separate in its report. They do not establish hosted PgSTAC/WAN or
  shared HTTP metadata-first rendering performance.

Maintained CI runs installed STAC serving/contracts on CPython 3.12 platform
cells and the CLI/browser workflow in Chromium. Configuration is not execution
evidence. Additional providers, authenticated search services, cloud credential
SDKs, WAN/large-file download performance and other platforms remain separate
qualification. This native adapter operates on a bounded startup selection and
stages whole checksum-pinned TIFFs on first metadata/render use. Range-efficient
immutable provider reads, sidecar graphs, SQL ingestion and automatic mosaics
remain outside its contract.

### Live HTTPS provider qualification

On October 6, 2026, the same installed wheel and frozen executable above pass
`tests/stac_live.py --browser` against **Earth Search** and **Microsoft Planetary
Computer**. Ignored `build/stac-live-verification/qualification-passed/` retains
the final report, native selection metadata, independent GDAL VRTs, Python/HTTP
images, Chromium screenshots and service logs. Earlier harness failures are
retained separately. No production-code changes were needed for this run.

Both providers select two historical Sentinel-2 observations over Venice using
bbox/time and known Item IDs. One-Item pages require three Earth Search requests
(including its terminal empty page) and two Planetary Computer requests. Result
ordering, empty spatial selections, time narrowing, explicit result-limit errors
and identity stability across page sizes pass. Selection timing is recorded
separately from asset transfer/render timing.

Four original coastal-band TIFFs total **14,149,161 bytes**, pinned in
`tests/fixtures/stac-live.json` after acquisition independently of RCFP. Each
256 × 256 Venice tile has **65,536 valid pixels** and **zero display-index
differences** from independent GDAL reads after STAC calibration. Python and the
regular HTTP tile route return identical PNGs. The shipped
`docs/examples/data/earth-search-source.json` serves the live source directly.
Chromium switches dates, restores revision-checked public SDK share links and
inspects display pixels for both providers without browser-side upstream calls.

Planetary Computer assets use its public signing API. An intentionally invalid
signature produces a real upstream 403, followed by a successful signing-callback
retry using a provider-issued valid URL. Public selection/dataset metadata and
service logs exclude asset locations and signatures. This does not qualify
private search authentication, cloud SDK credential chains, provider uptime,
WAN throughput or other collections/platforms. The two live provider cases have
no skipped checks. Strict Sphinx, 4,926 local links and source/wheel archive
boundaries also pass for the expanded guide and pinned example.

## Request cost and contention qualification

The Linux x86_64 / CPython 3.12 development wheel has SHA-256
`b544b03aa4857087cbc2ebe5746da2ddcd73474e6ccf5c98cafc346c1d2c04b8`.
Its executable has SHA-256
`92fa3471c50ccab74ce7bd677aa285d758e4af64fc803f0c2028375a053545b4`.
The shared core is
`1dc771e6b12c45b21601d61c3924725d2e3851c9a749687d0fb370d9e3254322`.
Ignored `build/request-cost/scheduling/` retains reports, source identities, logs,
commands and earlier failed checks separately. These results qualify the recorded
local artifacts. The raw Linux wheel remains outside portable release qualification.

- All **14 request-cost cases** pass against the installed wheel and executable.
  They cover slow/error sources, bounded admission, queued expiry without source
  access, active accounting after deadlines/disconnects, zero-retention render
  sharing, protected histograms/Prometheus output, trace/log redaction and X-Ray
  worker-stage propagation. Python capsule destruction joins active GDAL work
  while releasing the GIL. Unix SIGTERM, SIGINT and SIGHUP close native admission
  and wait for a blocked real raster read before exiting successfully, including
  a wildcard listener. Expired queued work never reaches its source during drain.
  The same regression fails against the previous executable, which exits while
  the read is blocked. See `signal-contracts/` and `signal-regression.log`.
- The same artifacts pass **53 installed contracts** and **249 independent
  bilinear rendering checks** in `signal-installed/` and `signal-rendering/`.
  Earlier artifacts separately passed 18 HTTP profile and seven cache-freshness
  cases. Their reports remain in the parent directory. These are not counted as
  repeated checks on the final artifact.
- The **180-second resource soak** completes **419,834 requests** without
  failures, overloads or timeouts. All **210,214** admitted jobs complete and
  active render sharing serves **209,625** followers. Accounting drains, with
  array and finished-image retention disabled. Peak observed RSS is **81.9 MiB**.
  Descriptors start at six, peak at 18 and end at nine. See `signal-resources/`.
  Correctness/build checks overlapped the soak, so it establishes resource bounds
  rather than throughput.
- Three alternating paired foreground/prefetch trials use two workers, zero
  array/image retention and 160 foreground requests per arm/trial. Pixels and
  accounting remain unchanged. Median p95 is **4.56 ms disabled / 5.81 ms enabled**,
  with viewport time **441 ms / 488 ms**. Prefetch does real neighboring reads
  but shows no foreground benefit in this workload. Retain opt-in background reads.
- Release build, formatting, all-target warnings-as-errors Clippy and **50 Rust
  unit tests** pass. The live histogram test covers concurrent observations and
  durations beyond the largest finite bucket. The ordinary suite separately
  ignores one encoder timing experiment requiring captured images. No existing
  dependency version was removed or changed when adding the pinned signal bridge.
  Its MIT/Apache notices are included in the staged bundle and wheel.
- Installed documentation extraction/check, strict Sphinx HTML and local links
  are checked separately. The maintained wheel workflow includes the contention
  gate on Linux x86_64 / CPython 3.12. Configured CI is not execution evidence.

The final source also passes **12 controlled scheduling runs**, comprising three
alternating candidate/control pairs for each workload. Its frozen candidate
executable is
`9dc285809b9012b4e2d49b9ccb11f0a4f5226b58f5a2d825145474181351bcfa`.
Each control differs in one source file and disables only the measured mechanism.
`signals-source-identities.json` pins the trees. `signals-paired/report.json`
retains every result from `tests/request_priority.py`.

| Mechanism                      | Control median viewport | Candidate median viewport | Control foreground p95 | Candidate foreground p95 |
| ------------------------------ | ----------------------: | ------------------------: | ---------------------: | -----------------------: |
| Discard expired queued jobs    |                  508 ms |                    132 ms |                 514 ms |                   135 ms |
| Share identical active renders |                  344 ms |                    122 ms |                 357 ms |                   132 ms |

All four foreground requests per viewport succeed with identical image bytes
between arms. Budgets are one raster worker, 16 queue slots and zero array/image
retention. Response deadlines are identical within each pair. Accounting drains
without failures or overloads. Peak observed RSS across all arms is **102.4 MiB**
and descriptors peak at **20**. Controls admit the expected extra work. Discarded
jobs never access their obsolete sources. The origin serves checksum-pinned real
Sentinel pixels and delays obsolete-source HEAD requests by 75 ms. These small
loopback samples establish benefit for those contention patterns, with independent
rendering correctness qualified separately.

Earlier sandbox failures, missing runner dependencies, observer contention,
build/lockfile setup, a control retaining unexpected active sharing and the
corrected 150 ms cold-read test deadline remain separate negative evidence.
The correctness runner uses 500 ms. Paired latency measurement remains separate.
Hosted OS/ISA and additional Python cells, repaired-wheel portability, actual
S3/WAN traffic and browser viewport completion were not executed here. Windows
console draining and forceful OS termination are outside the Unix signal gate.
Python/WSGI/Lambda network delivery remains host-owned. Stage and monitoring scope
are documented in [monitoring](../docs/reference/monitoring.md).

## Native layer qualification on 2026-10-06

Local Linux x86_64 / CPython 3.12 checks use GDAL 3.10.3 and the
`rcfp-native-1` SQLite catalog. Reports, commands and retained failure logs are
in ignored `build/multiband-assets/`. Inputs derive from the checksum-pinned
real raster corpus with narrowly documented mask/alpha/declaration edges.

The tested wheel is
`rcfp-0.1.1.dev10+g017bcf8f3.d20261006-cp312-abi3-linux_x86_64.whl`,
SHA-256 `a174dfedcfc807742eaa6285de9f1a1b19e00f9e42096874688682988b2f0d00`.
Its executable SHA-256 is
`b1f64dd817b1de92164b02dca92d011f579e241d7a5153016fc8de8eb806a431`
and shared-core SHA-256 is
`2690156ed4c33df12bf65ccadcce010b680600a1bfa4f349c157f503fa1755b2`.

- All **51 installed layer cases** pass, with no exclusions. They check
  database-stored definitions/defaults, metadata, singleband, packed/separate RGB,
  multispectral expressions, raw/scaled arithmetic, channel masks/nodata, binary
  and fractional alpha, mixed resolutions, explicit grids/resamplers, vertical
  unit metadata, categorical palettes, selection/overlap, invalid input, atomic
  registration, import/source preservation and shared Python/HTTP/CLI/WSGI behavior.
  Samples and decoded colors are checked independently with Rasterio/NumPy.
- All **53 existing public wheel contracts**, **15 compatibility contracts**
  including the pinned Terracotta round trip, **18 profile/admission cases**,
  **7 cache freshness checks**, and **26 HTTP reference comparisons in each
  profile** pass against the recorded wheel or its executable. Native HTTP
  reference comparisons explicitly disable direct reads.
- Release build, **50 Rust unit tests**, warnings-as-errors all-target Clippy,
  Rust formatting and applicable Python lint/format checks pass. The ordinary
  Rust suite separately ignores one explicit PNG timing test.
- Installed reference extraction/check, strict Sphinx HTML, **4,771 local links**,
  documented examples and two-way catalog/migration rehearsal pass. The source
  archive/wheel boundary check preserves Hatch VCS and includes native layer
  code/tests/docs while excluding developer context.

Initial mask/alpha failures, sandbox socket failures and the corrected independent
reference calculation remain retained separately from successful runs. Native-only
layer runs explicitly exclude installed Python/WSGI/cache checks. The final
installed run executes them. These results do not qualify hosted OS/architecture
or additional Python cells, repaired-wheel portability, remote native catalog
storage or cloud deployment. The maintained native and wheel workflows include
the layer runner. That configuration is not execution evidence.

## JavaScript SDK and viewer qualification on 2026-10-06

Local Linux x86_64 qualification uses Chromium **140.0.7339.16**, Node **22.18.0**,
TypeScript **5.9.3** and the checksum-pinned real raster corpus. The isolated
executable is SHA-256
`70863f255a56b59c2b75365cdea05b9e2520c7b0e5a33140f903fd00e4f1d470`.
Its embedded SDK and viewer assets match source bytes. The installed wheel's
executable matches that isolated executable byte-for-byte.

The tested wheel is
`rcfp-0.1.1.dev10+g017bcf8f3.d20261006-cp312-abi3-linux_x86_64.whl`,
SHA-256 `d6f7f5ec0dc1ff5d72c268d441fc69fee758260433680e13b8122719f0dd58c4`.
The source archive SHA-256 is
`bbd2c292c4ccbeeb84ea034c6cf8a472c4a082fa34d07be56f442228f3de841a`.
Commands, reports, screenshots, artifact identities and review notes are retained
in ignored `build/sdk-validation/`, with final evidence indexed by its
`verification.md`.

- All **37 Chromium checks** pass against the isolated executable and again
  against the installed wheel, using both compatibility profiles. They exercise
  discovery, singleband/RGB/expression rendering, legends, metadata, explicit
  time-key browsing, display-pixel inspection, share/restore and hash navigation,
  source/profile mismatch, encoded keys including literal braces, aborts, stale
  responses, errors/recovery, allowed/denied CORS and cross-origin SDK imports.
  Keyboard/mobile controls, native default-layer display/inspection and three
  independent Leaflet/MapLibre/OpenLayers applications also pass. External
  requests are blocked during these workflows.
- All **11 SDK tests** and strict declaration checks pass. An offline npm package
  install, typed consumer and runtime import exercise both public exports.
- All **50 Rust unit tests**, warnings-as-errors all-target Clippy, **18 profile
  contracts** and **26 pinned Terracotta HTTP reference comparisons** pass.
  The ordinary Rust suite separately ignores one explicit PNG timing test.
  SQL feature integrations are outside this run.
- Wheel/source builds, archive boundaries, Hatch VCS retention and installed
  reference extraction pass. Strict Sphinx HTML and **4,771 local links** pass,
  as do applicable formatting/lint checks and the product-tree identity scan.

Earlier setup failures and regression failures remain separate from these passes.
Final artifacts come from an isolated Cargo directory because concurrent work
replaced shared build outputs. Inspection reports in this qualification display RGBA rather than raw
source values. Time labels reuse
real pixels and do not establish acquisition correctness. External basemaps,
Firefox/Safari, formal screen-reader/WCAG assessment, repaired-wheel portability,
additional platform cells and cloud deployment remain unqualified. npm registry
publication has not been performed.

## SDK release workflow qualification on 2026-10-06

The independent SDK release path has local evidence in ignored
`build/sdk-release/verification.md`. Node **20.20.2**, **22.18.0** and **24.21.0**
each pass the **11 SDK contracts**, **4 release-policy tests**, strict TypeScript
**5.9.3** checks and installed-package smoke test using npm **11.6.2**. All three
produce the same eight-file tarball, SHA-256
`f551e08c2914a59906895aa23c2a25f0f1914f5a5d741dc563583f752ea0dfbb`.
The exact-file boundary excludes viewer internals, private test tools and runtime
dependencies. Package files match source bytes after an offline install.

The rebuilt executable, SHA-256
`52a9eef89388eef858a284f676d2e9900e2f5953bcf8847edfd1d5d372f0f240`,
passes **37 Chromium checks** and the **24 retained STAC serving/browser cases**.
Its embedded SDK/viewer files match source. Actionlint **1.7.12** passes the
reusable qualification, npm release, correctness and Python release workflows.
The tested-tarball publication command passes with `--dry-run`. Strict docs,
**4,922 links across 52 pages**, source archive boundaries and applicable SDK
format/lint checks pass. Archive validation pairs the fresh source archive with
the previously qualified Python wheel to check boundaries, without claiming a
new wheel qualification.

The temporary-repository Hatch VCS regression passes Python releases with
intervening stable/prerelease SDK tags. Without the Python-only Git discovery
command, the same test fails on an SDK tag before the fix. The maintained test
runs in shared qualification.

The GitHub `npm` environment exists and permits deployments only from `sdk-v*`
tags. npm scope ownership, initial package publication and trusted-publisher
account configuration remain unverified. No registry write or hosted Actions run
was performed. Dry-run publication does not verify OIDC. Failed setup/fixture
checks are retained separately. A repository-wide Rust formatting check reported TIFF formatting failures
outside the SDK release changes. Those files were left unchanged.

## Exact TIFF access qualification on 2026-10-06

Native mode uses async-tiff 0.4.0 for eligible TIFF headers and exact stored-pixel
reads, with GDAL fallback. Statistics, detailed asset metadata, general
resampling/reprojection and the named Terracotta profile retain GDAL.
`TC_ASYNC_TIFF=false` restores GDAL source access. The selection, validity and
resource boundaries are documented in [the evaluation](async-tiff-evaluation.md).

Local qualification uses Linux x86_64, CPython **3.12.11**, runtime GDAL
**3.10.3**/PROJ **9.7.1**, and independent Rasterio **1.4.4** expectations from
checksum-pinned real rasters. Casts and format derivatives preserve source
pixels. Explicit Mercator coordinates isolate grid selection. Three nonfinite
pixels and malformed TIFF declarations provide narrowly synthetic edges.

The frozen executable SHA-256 is
`0eac34a53d8970dd7974c42abbe39fc0e0ea6aeafcfefa2459b5b1115d5abb3e`.
The core library SHA-256 is
`d62f0446976764815aa19e9b6ac0ed281f82e280275afb97b43dfbd2b598a837`,
matching the installed repaired wheel's library. The wheel is
`rcfp-0.1.1.dev10+g017bcf8f3.d20261006-cp312-abi3-manylinux_2_39_x86_64.whl`,
SHA-256 `ae506ff7d59ef32b23081128a9a69c481ddb862f5c9f23eeaeb94726f8606540`.
The source archive SHA-256 is
`da316cffd0ef935ea11635e476800a6209c803ec46a81d2bdefff81003fce863`.
Commands, identities, raw timings and reports are retained in ignored
`build/async-tiff-adoption/`, indexed by `verification.json`.

- **328 exact windows**, **19 grid checks** and **8 fallback checks** pass for
  local and controlled HTTP sources. Coverage includes lossless codecs, both byte
  orders, scalar dtypes, planar/chunky storage, masks/alpha, overviews, source
  invalidation, strong ETag enforcement and malformed responses/layouts.
- **3,646 raster boundary cases** and **996 rendering cases** pass against
  independent expectations. Source identities remain unchanged during the final
  runs. The rendering matrix covers nearest, bilinear, cubic and average.
- The final installed wheel passes **53 public contracts**, **15 Terracotta
  compatibility contracts**, **25 STAC contracts**, **29 serving contracts**,
  **51 layer contracts** and **14 request-cost contracts**.
- **54 ordinary Rust unit tests** pass. Two fixture-dependent TIFF tests are
  separately executed by the qualification runner. All six focused TIFF tests
  pass. One explicit PNG timing test remains ignored. Locked offline all-target
  Clippy with warnings as errors, Rust formatting and TIFF runner lint pass.
- Wheel repair, wheel/archive boundaries, license inventory, Hatch VCS retention
  and installed reference extraction pass. Strict Sphinx and **4,924 local links
  across 52 pages** pass for the operator documentation. The product-tree scan finds no stale
  development labels or workspace paths. Repository URLs remain intentional.

The three-trial aligned-read microcheck measures faster first access and slower
warm access, as recorded in the evaluation. It does not qualify general
navigation or WAN throughput. GDAL/PROJ remain bundled runtime dependencies.
Authenticated/cloud SDK providers, other operating systems/architectures,
additional Python cells, hosted CI and the release portability floor remain
unexecuted. The serving run excludes its optional Chromium workflow. Earlier
setup and regression failures remain separate from passes. No publication or
cloud deployment was performed.

## Production TIFF navigation qualification on 2026-10-06

The executable and core library from the exact TIFF qualification remain
unchanged. Three alternating paired trials of native singleband/RGB catalog
navigation pass **324 fresh service runs and 61,236 tile responses**, with no
failed requests or changed decoded images. Local original scenes, existing
Mercator derivatives and a versioned 15 ms HTTP origin cover one user and three
users with shared/separate views, first viewports, pans, zooms and revisits.

Backend usage was inferred incorrectly from the shared raster-read timer, so
the zero-TIFF claim for the **17,102 candidate raster jobs** is unsupported.
Default-sampling first-view
completion is approximately **14–20% slower** for original scenes and
**10–15% slower** for prepared scenes with TIFF enabled. Grouped throughput is
**2–7% lower** and peak service RSS **4–5% higher**. Per-cell timings and tails,
CPU, source bytes/requests, explicit cache budgets and read-count checks are
retained in ignored `build/async-tiff-navigation/report.json` and `summary.md`.
The [evaluation](async-tiff-evaluation.md) records scope and interpretation.

Removing only descriptive tiling
metadata from prepared elevation preserves all **513 independently checked
blocks across six levels**. Backend usage was not established. A separate
arithmetic replay shows the overview guard rejects floating-point values just
below integer scale factors. No sampling or reference outputs were regenerated.
The initial prefetch smoke and superseded run are excluded from final evidence.
This benchmark does not change TIFF production code/settings. It qualifies the
frozen executable, with subsequent workspace changes recorded separately. Native-layer/
STAC-specific serving, browser paint, cold disk, WAN/S3, authenticated providers,
hosted CI and other platforms remain unmeasured. These results do not qualify
default-on use as a navigation performance improvement.

## Confirmed TIFF pixel navigation qualification on 2026-10-06

The frozen executable with explicit pixel-reader counters has SHA-256
`c81be35999667b290db901bcb8a53e23bc4cfcf4419ddfd7b98bfd6cf2b4bccf`.
Its core library has SHA-256
`fc7b5118dd90088d820b60e8feb00a43e2e05e55fa4c8660fcedd32ed24f844f`.
Its source snapshot includes workspace changes present at capture. Changes made
after capture do not qualify under these artifact hashes.

Three alternating paired trials pass **108 fresh native HTTP service runs and
13,608 responses**. All **3,690 candidate pixel-reader invocations use TIFF**,
with zero GDAL pixel fallback in every viewport. The control has 3,690 GDAL
invocations and zero TIFF invocations. Every **6,804 paired response** has equal
routes, dimensions and decoded pixels. Independent Rasterio/NumPy checks pass
**2,214 images across 153 unique reference tiles** with exact masks and colors.
Independent source comparisons pass **6,930 value/mask blocks** at all stored
levels. Imagery has no transparent responses. Elevation contributes 252 across
all variants/trials, and every cell's median tile is fully populated.

Deliberately eligible real-scene derivatives have descriptive metadata removed
and affine adjustments below eight nanometers across each footprint. Both
variants use these same inputs and nearest sampling of stored base/2× overview
pixels. Local first viewports improve **43%** and active-request throughput
**27%**, with **23% lower CPU** and **8% lower RSS**. HTTP elevation improves,
but imagery pans/zooms and RGB regress. Grouped HTTP throughput is **19% lower**.
The [evaluation](async-tiff-evaluation.md) records workload-level results and
qualification limits. These results establish eligible-path performance without
qualifying source eligibility rates, WAN/S3, native-layer/STAC-specific serving,
cold disk, browser paint or additional platforms.

The frozen snapshot passes **54 Rust unit tests**, with two fixture-dependent
tests explicitly ignored, release Clippy with warnings denied, and formatting.
Python benchmark helpers pass Ruff. The TIFF decoder, selection rules and default
setting are unchanged. Explicit JSON/Prometheus counters and benchmark usage
gates are the production/test additions. Strict Sphinx and **5,240 local links
across 55 pages** pass for the documentation. Reports, commands, input recipes, source
identities and raw results are in ignored `build/async-tiff-active/`. The initial
mostly-nodata imagery run and smoke timings are excluded. Snapshot build/argument
setup failures and sandbox socket failures remain separate from successful reruns.

The HTTP origin in this matrix had Nagle enabled. A transport diagnostic on
2026-10-07 changes only origin `TCP_NODELAY` and passes **24 services and 1,296
responses** over three balanced paired trials of one-user singleband/RGB imagery.
All **864 candidate reads use TIFF**, with zero GDAL fallback and identical decoded
pixels across all four backend/origin variants. TIFF request counts and bytes
remain identical. Disabling Nagle changes RGB first viewport from **501 to 177 ms**
and median pan from **135 to 53 ms**. Corresponding GDAL timings change from
293 to 190 ms and 79 to 47 ms. This identifies a substantial origin transport
contribution to the matrix's HTTP regressions. The maintained origin disables
Nagle. The diagnostic does not replace the full multi-user/elevation matrix or
qualify WAN/S3. Packet capture and the remaining request-strategy cost are not
established. Evidence is retained in ignored `build/async-tiff-mechanism/`.

## TIFF scheduling decision on 2026-10-09

The additional remote-window scheduler is **no-go**. The existing default native
TIFF reader remains in production. Candidate source trees and the integration
draft are removed. Frozen evidence is archived in ignored storage. No new
production switch or inactive scheduler is retained. The
[evaluation](async-tiff-evaluation.md#scheduling-decision-on-2026-10-09) records
the decision and workload limits.

The eligible and ordinary-input comparisons each complete **108 services,
13,608 responses and 6,804 exact ordered pairs**. Eligible remote RGB first
views are 1.3–1.7 times faster and zooms 1.8–2.4 times faster. Both eligible
variants perform 3,690 TIFF pixel reads and zero GDAL pixel reads. The ordinary
map-navigation corpus performs **zero TIFF pixel reads** in either variant,
so this candidate supplies no pixel I/O concurrency benefit for that corpus.
Paired eligible peak RSS does not increase materially. Median remote CPU rises
about 3%. Original ordinary-input timing includes regressions. Additional
five-trial and three-trial repetitions have unstable timings under host
contention and do not establish the no-regression gate. Their 12,096 ordered
pairs match exactly, with all pixel reads using GDAL. Their timings are excluded
from adoption evidence.

Candidate and serial control each pass seven HTTP lifecycle cases on independently
prepared, ordinary 10 m Mercator layer grids: exact rendering and retention,
queue overload, deadlines, version changes, origin failure, client disconnect
and shutdown. Each correctness case checks 313 independently expected PNGs.
One candidate worker overlaps four windows/network reads. Control concurrency
is one. Both drain admitted accounting and retain stable descriptors. This
qualifies those frozen executables, not the current working tree or a broader
transport/resource envelope.

The compared executable SHA-256 values are:

- Serial: `43d26e8c3e21203883dda6e24bfb2113cdf0e9c03d51975c67515cb42f6b3f7e`.
- Concurrent: `48682fb4abc91d74bd183545675917be2a1310ddecdaeddb33a241fa56496a68`.

`build/async-tiff-decision/decision.json` indexes verification, source archives,
resource ratios and lifecycle reports. The initial deferred-job smoke failure
and two lifecycle setup failures remain excluded. New concurrent array unit
assertions were not executed. Prior pipeline fixture/unit/Clippy results belong
to that earlier artifact. WAN/S3, authenticated transport, additional platforms
and larger decode workloads remain unqualified. No cloud upload or spending
was required.

## Experimental TIFF pipeline

The isolated service snapshot in `build/async-tiff-pipeline/` evaluates concurrent
windows, separate decode tasks and cross-window range merging on 2026-10-07.
The timed executable SHA-256 is
`c31b833ece9c2d8ed372ff0479eca32f016ff1215cf1de1da0ae428e1e6a49d6`.
Production scheduling and defaults are unchanged.

Three paired trials against the current TIFF adapter pass **108 services and
13,608 responses**, with all **6,804 ordered pairs** matching exact pixels.
Both sides perform **3,690 TIFF pixel reads and zero GDAL pixel reads**.
Three paired trials against GDAL pass **54 services and 6,804 responses**, with
all **3,402 ordered pairs** matching. The candidate uses TIFF for all 1,845 reads.
The control uses GDAL for all 1,845 reads. Three clean rotated ablation trials
pass **5,832 ordered candidate/control comparisons**. Overlapping remote I/O
supplies most of the RGB navigation improvement. Extra decode scheduling and
range merging supply little additional benefit on these aligned inputs.

Independent Rasterio/NumPy checks pass **2,214 images across 153 unique reference
tiles** with exact masks and colors. Pinned qualification passes **328 window
cases, 19 grid cases and eight fallbacks**, including concurrent output checks.
The complete unit suite passes **62 tests**, with two fixture-dependent tests
passing separately in that qualification. The merged-range test verifies slices
and source-version failure propagation. Release warnings-as-errors Clippy and
formatting pass. The [evaluation](async-tiff-evaluation.md#concurrent-service-tiff-pipeline-on-2026-10-07)
records timing, resource regressions and scope limits.

This qualifies the experiment's measured behavior, not a production scheduler
change. Whole-service cancellation under saturation, larger decode workloads,
WAN/S3, authenticated storage and other platforms remain unqualified.
`summary.md`, JSON reports, source changes, the frozen executable and separately
labeled source hashes are retained in that ignored directory. Initial test setup
and lint failures, exploratory timings and an ablation trial that overlapped oracle
calculation remain separate from the successful runs. The contaminated ablation
trial is excluded and replaced with a clean trial.

## Lambda demo preparation

`packaging/demo/` supplies CloudFormation storage/runtime stacks, an AL2023
container recipe, checksum-pinned catalog preparation, CLI deployment/removal
helpers and a static export of the bundled viewer. HTML documentation builds
include the viewer under `demo/`. GitHub Pages publication is opt-in. The public
backend uses database-backed S3 paths with precomputed metadata. It does not use
the STAC adapter's whole-file staging path.

Local CloudFormation validation with cfn-lint 1.40.4, strict Sphinx HTML and local
link checks pass. The persistent native Runtime API adapter and Chromium pass
discovery, exact decoded preview pixels, allowed/disallowed CORS, preflight,
missing datasets, Pages project paths, RGB, share links and the Leaflet SDK
example. The preview reference was acquired independently with Terracotta 0.10.1
and GDAL 3.10.3 for the checksum-pinned demo crop. Reports and the frozen native
bundle are in ignored `build/demo-validation/`.

The official standalone AWS Runtime Interface Emulator v1.37 also passes
discovery and initial/repeated exact previews with CORS on the native host.
Its SHA-256 is `6b1e686e62ab2baf5759c412c4864276ef2a88b094fca53ec070637ccba9b9a5`.
The tested executable SHA-256 is
`0f85422c336ad55b90920566071a892a17aded384ddf5556d090e7310cafade0`.
Its provenance and report are in the same ignored validation directory.

Docker is unavailable locally. The container build and its AL2023 emulator lane,
GitHub Pages publication and AWS function/S3/role behavior remain unexecuted.
The demo workflow prepares the image/emulator checks without AWS credentials.
CloudFormation deployment/removal helpers have not been run against an account.
No resources were provisioned or published. Fresh image and real-service checks
are required before calling the demo qualified or drawing cloud latency claims.

## Native keyed catalog qualification on 2026-10-07

The `rcfp-native-2` catalog stores logical band bindings, expressions, display
defaults and resolved STAC selections in SQLite. Separate files, packed TIFFs,
direct STAC and database snapshots share keyed discovery, metadata, array and
tile APIs. The advanced `rcfp-native-1` layer interface remains supported and
also projects its channels into keyed discovery. Native keyed writes require
local SQLite. Original Terracotta catalogs retain their format.

The frozen executable SHA-256 is
`11b0994278452b9ba71273f4fb62da513bb44312eb4a78a48db674e91ed292aa`.
The core library SHA-256 is
`b041f1d15f1e0ca3236c0b3a197edc5823c6e5c11ba1e849b38e60237ea0195e`.
The installed CPython 3.12 Linux x86_64 wheel contains these exact binaries.
Its SHA-256 is
`9932d94d929f5abf30fd92edd8884e10e20b8fa5d90b4e751e982e9b564cee3a`.
Artifact identities and reports are in ignored `build/catalog-reuse/verification.json`.

All **274 contract checks** pass against those artifacts. They include **23
keyed catalog cases across six source layouts**, **51 advanced layer cases**,
**30 STAC serving cases including Chromium**, **25 STAC selection cases**, and
the public, compatibility, profile and cache suites. Both pinned Terracotta
differential profiles pass **26 cases** each. Keyed cases use checksum-pinned
Sentinel crops and independent Rasterio/NumPy references for selected metadata,
calibration, masks, decoded images, integer overflow, and chained integer,
boolean and float16 expressions. They also exercise WSGI/Lambda, persisted
defaults, atomic rollback, cache invalidation and serving after the source
metadata documents are removed. Raster bytes remain external to the database.

Rust passes **54 unit tests**. Two TIFF tests requiring the separately prepared
fixture matrix remain explicitly ignored. Clippy with warnings denied, Rust
formatting and Ruff pass. Strict Sphinx builds **55 pages** and checks **5,240
local links**. Executable documentation examples pass, including native HTTP,
WSGI, Lambda and the upstream catalog read/write and migration roundtrip.
Source archive/wheel boundaries and Hatch VCS versioning pass. The wheel
workflow includes the keyed suite in its CPython 3.12 platform cells. This is
prepared CI, rather than evidence of execution on additional platforms.

Earlier debug wheels and superseded runs are excluded from this qualification.
Hosted providers, cloud credential-chain integration, deployment, WAN performance
and other operating systems/architectures remain unexecuted for this snapshot.
No new navigation performance claim is made. Derived statistics remain sampled
preview statistics, and their extent polygon is not an exact valid-data hull.

## Disposable cloud host preparation

`packaging/cloud-test/` defines separate CloudFormation storage and host stacks,
with Ubuntu 24.04, an on-demand C7i instance, restricted HTTP ingress, SSM access
and instance-role S3 permissions. The CLI prepares complete native bundles and
copies precomputed Terracotta-format SQLite catalogs into deployment artifacts.
Self-contained local TIFFs become digest-addressed S3 objects. Original catalogs
are preserved. Stored native bindings and STAC source catalogs need separate
staging. A guest timer uses an absolute deadline, attempts report collection and
terminates the host. Retained storage has explicit removal commands.

All **12 credential-free checks** pass against the native executable with SHA-256
`11b0994278452b9ba71273f4fb62da513bb44312eb4a78a48db674e91ed292aa`.
They cover catalog copying, bundle extraction, HTTP viewport requests, exact
independent preview pixels, Chromium first view/pan/zoom/revisit and simulated
provisioning, archive, command and collection failures. Expired-host report
retrieval and missing-report failures are separate cases. Invalid image and
reference controls fail as intended. Initial browser harness failures remain
separate from final passes. Reports are in ignored `build/cloud-test-validation/`.

cfn-lint 1.40.4 validates both templates. Bootstrap shell syntax, the UTC calendar
expression, Ruff and formatting pass. Strict Sphinx builds **56 HTML pages** and
checks **5,396 local links**. Source archive/wheel boundaries and Hatch VCS
requirements pass. The qualification workflow builds and retains a native
bundle without AWS credentials. Prepared CI is not evidence of execution.

AWS pricing/image lookups, stack provisioning/removal, SSM connectivity, private
S3 reads through instance credentials and scheduled termination remain
unexecuted. Docker is unavailable locally, so the AL2023 build in the workflow
has not run here. Local runtime checks use loopback HTTP on the native host and
do not qualify the Ubuntu AMI, WAN latency or EC2 performance. No cloud resources
were provisioned. The operator procedure is in
[cloud test guidance](../docs/how-to/cloud-test.md).

## Native discovery, point values and explicit grids

Local Linux x86_64 qualification on 2026-10-07 uses the installed CPython 3.12
wheel `rcfp-0.1.1.dev11+gc61190725.d20261007-cp312-abi3-linux_x86_64.whl`.
Its SHA-256 is
`4c722efb6a2b6c7e6083f289b88e5ec0e764995d937068afa04dc4cd55bf4196`.
The bundled executable is
`319e64822bc93daef6a0708f90bdd0dfc4cef21e8fc08ddaa001cf2c03c5eabd`
and shared core is
`a6cd21165b47950f126379e14870d50ddc22abb5072ef9c9fb5f615c53149903`.

All **55 native service cases** pass. Source points match independent Rasterio
reads for physical bands, calibration/units, rotated affines, fractional alpha,
nonfinite samples, typed expressions, out-of-source coordinates and half-open
cell boundaries. Discovery covers SQL, keyed native, STAC and advanced-layer
projections across Python, WSGI and Lambda, including slash-containing keys and
deployment prefixes. TileJSON bounds, attribution, zooms, defaults and style
parameters are checked against stored declarations.

Independent nearest-neighbor reprojection checks exact decoded pixels and masks
for Mercator, geographic, Arctic and southern stereographic grids, including
source windows on both sides of the dateline. Cache reuse, matrix separation
and conditional/pinned image responses pass. The four matrix descriptors also
validate against downloaded official OGC TMS 2.0 schemas, whose hashes are
retained with the local report. OpenLayers completes visible geographic/polar
renders using grids constructed from those descriptors. The separate browser
suite passes **37 checks**, including Leaflet, MapLibre and OpenLayers consuming
the native TileJSON manifests and the existing named-profile examples.

The same frozen artifacts pass **23 keyed catalog cases**, **51 advanced-layer
cases**, **53 public contract cases** and the **26-case pinned Terracotta HTTP
differential** in the named profile. Rust passes **61 tests**. Two fixture-dependent
TIFF tests and one image timing benchmark remain explicitly ignored. Clippy with
warnings denied, Rust formatting, Ruff, SDK contracts and TypeScript checks pass.
Strict Sphinx builds **61 pages**, checks **6,058 local links**, and executable
documentation examples pass against the installed wheel. Archive checks preserve
Hatch VCS versioning and exclude developer context. Reports and initial failed or
superseded runs are retained separately in ignored `build/native-services/`.

Qualification covers the pinned windows and declared sampling policies. The
southern polar view uses a source near 34°S, rather than an Antarctic acquisition.
Hosted deployments, remote providers, WAN behavior and other platforms remain
unexecuted. Prepared wheel CI is not execution evidence. TileJSON 3.0 and OGC
matrix descriptors are the supported interfaces. WMTS and OGC API service
conformance are not claimed.

## Rendered publication and reuse

Local Linux x86_64 qualification on 2026-10-07 uses the installed CPython 3.12
wheel `rcfp-0.1.1.dev11+gc61190725.d20261007-cp312-abi3-linux_x86_64.whl`.
Its SHA-256 is
`85f74eeaae63c265b5a34798c0c67c393679570f0e11d79670ebc2649fd61d72`.
The executable is
`f8be8738fda624cbdeec960ec857e050bd69f9047136da7de9d149af99ef7322`
and shared core is
`8c725d19e8a63e61a5dd07849497b974a4de01e6efa6d89ce0fba75f0ee3e8b1`.

All **101 delivery checks** pass across Python, WSGI, native HTTP and Lambda
events. They cover identical validators, strong/weak conditions, HEAD, CORS,
private/public publication, authorization/cookie isolation, empty images,
errors, style/grid/encoding changes and catalog/source invalidation. A source
write preserving both file size and modification time rejects the old pinned
identity. Compute and registered-layer cache hits avoid raster submissions.
Native matrix tiles receive the same conditional and revision policy. The named
Terracotta profile retains its headers and ignores conditional requests.
Independent Rasterio/NumPy expectations verify decoded pixels. nginx 1.28.3
serves the documented configuration, and Chromium loads the downloadable example
with exact canvas pixels and a retained screenshot.

The runner records **30 measurement cells** across three alternating paired
nine-tile viewport trials. The source derivative retains checksum-pinned real
Sentinel pixels with explicit Mercator geometry. With zero encoded retention,
median first/revisit/conditional times are 37.81/7.36/6.36 ms, with nine raster
jobs at each stage. With 4 MiB retention they are 37.06/5.13/3.05 ms, with
nine/zero/zero jobs and 521,324 accounted retained bytes. Each image viewport
transfers 245,731 payload bytes. Conditional responses transfer zero. End-stage
origin RSS spans approximately 71–76 MB and is not a peak-memory measurement.
Eight simultaneous identical requests produce one raster job and seven
coalesced followers at both budgets in every trial. Pinned nginx revisits hit
the proxy cache without origin requests, while authorization/cookie requests
bypass it. These loopback measurements qualify this workload only.

The same frozen artifacts pass **53 public contracts**, **14 compatibility
contracts**, **51 advanced-layer cases**, **25 STAC cases** and **seven cache
freshness cases**. Both HTTP profiles pass the **26-case pinned Terracotta
differential**. Rust passes **61 tests**. The ordinary run separately ignores
two fixture-dependent TIFF tests and one image timing benchmark. The dedicated
TIFF runner passes 328 windows, 19 grids and eight fallback cases. Clippy with
warnings denied, Rust formatting, Ruff, workflow lint and archive checks pass.
Strict Sphinx builds **61 pages** and checks **6,061 local links**. Archives
preserve Hatch VCS requirements and exclude developer context.

Reports, artifact/source hashes, superseded runs and initial failed setup or
regression checks are retained separately in ignored `build/rendered-caching/`.
The Linux wheel workflow includes nginx/Chromium delivery checks. Prepared
CI is not evidence of hosted execution. Other platforms, hosted Lambda runtime,
cloud/CDN/WAN behavior and authenticated remote providers remain unexecuted.
Unversioned remote freshness retains the documented TTL boundary. The origin
revision guard does not archive old sources or purge external immutable caches.

## Installed diagnostics and offline first use

The repaired Linux x86_64 wheel passes **19 diagnostic contracts**, with zero
failures, errors or skips. Checks cover read-only catalogs, selected physical
bands, configuration origins and redaction, ingestion destinations and key order,
layout warnings, HTTP range/access failures and bounded native/network probes.
The wheel SHA-256 is
`19e776214299b4245dd1ea497b80d34fbcc61a95c04dd093f0bd1767e7894841`.
Its executable and core match the frozen bundle byte for byte.

An automated first-use trial runs outside the checkout, exports the supplied
checksum-pinned real imagery, creates a catalog, serves a preview and displays
the map in Chromium with **zero external requests**. Decoded preview pixels
equal the pinned independent Terracotta reference. The recorded trial takes
**1.473 seconds** and reports the sample's missing overviews and full-width
strips as nonblocking warnings. This is automated independent-reference evidence.
No human-participant usability trial has been performed. A separate environment
containing only the installed wheel and NumPy also creates the catalog and exact
reference preview, without Rasterio, Pillow or browser-test dependencies.

The frozen artifacts also pass **53 public contracts** and **18 profile
contracts**. Rust passes **61 tests**, with two fixture-dependent TIFF tests and
one image timing benchmark separately ignored. Clippy with warnings denied,
changed-file formatting, Ruff, workflow lint, installed reference extraction and
archive checks pass. Strict Sphinx builds **61 pages** and checks **6,061 local
links**. Source/wheel checks preserve Hatch VCS requirements and exclude developer
context. Diagnostic/browser checks are prepared in the docs and wheel workflows.
This is not evidence of hosted CI execution.

Artifact hashes, reports, the browser screenshot, minimal-dependency checks and
superseded failures are retained separately in ignored `build/first-use/`.
Additional platforms/Python versions, private-provider IAM access, hosted CI and
Docker/Lambda image execution remain unexecuted. Docker is unavailable locally.

An additional independent AI-user trial uses a subagent with no conversation
history, an empty working/home directory, a fresh Python environment, the same
repaired wheel and only public HTML documentation. The application is installed
offline with NumPy as its only dependency. Browser tooling is isolated separately.
The participant does not consult source, maintained tests, prior reports or
design context. Installation finishes at **27.7 seconds**, a useful image is
produced at **40.2 seconds**, and a useful selected browser map is visually
inspected at **162.2 seconds**. Overall time including evidence preparation is
**374.2 seconds**. Grayscale/RGB rendering, center inspection and share restoration
work. This is an independent AI-user trial, not a human-participant trial or a
performance benchmark.

The participant records a basic first-use pass with obstacles. Loopback sandbox
permissions and headless browser launch require environment workarounds. The
viewer makes zero external request attempts, but the separate API-docs page
attempts two blocked decorative assets from Google Fonts and the Redoc CDN.
One RGB zoom after share restoration returns six HTTP 503 tile responses and
leaves visible gaps. The trial preserves this failure without server tuning or
root-cause investigation. It does not qualify zoom reliability. Independent
reports, commands, request logs and success/failure screenshots are retained in
ignored `build/first-use/independent-agent/`.

## Offline map interaction qualification

The current Leaflet adapter shares three tile requests across view changes,
drops removed queued tiles and bounds retries for busy responses. This keeps
RGB viewport bursts within the default raster capacity without increasing
server limits. The API-docs renderer uses local system fonts and a text footer
label. Its native service description is corrected. The named compatibility
profile retains its reference specification.

The repaired Linux x86_64 wheel SHA-256 is
`af1272e9bcb7b8ad139b0fc5d91eaaf84ba80191cb16e4cfd2bb0f758f9a93a3`.
It passes **19 diagnostic**, **53 public-package**, **18 profile** and **37
browser** checks. Both profiles pass **26 pinned reference comparisons** each.
The SDK passes **13 tests**, including shared admission, busy-response retry,
queued/active removal and permanent errors. Strict TypeScript compilation,
Clippy with warnings denied, formatting, Ruff, installed reference extraction
and archive checks pass. Strict Sphinx builds **65 pages** and checks **6,758
local links**. The RGB regression waits for each zoom animation and successful
decoding of every tile at default server settings. API-docs monitoring includes
requests blocked before routing by CSP and observes no external attempts.

A further independent AI-user trial starts without conversation history, using
only public documentation, a fresh home/working directory and the supplied raw
Linux wheel. Its executable and core match the final repaired installed wheel
byte for byte. Installation completes at **30.8 seconds**, a useful PNG is
created at **36.4 seconds**, and the documented selected browser map is captured
at **152.0 seconds**. Overall time including evidence preparation is **301.7
seconds**. Grayscale/RGB, share restoration in a fresh browser context, ordinary
zoom, center inspection, API docs and all three SDK examples pass visual
inspection. All **405 observed HTTP responses** succeed, with **zero external
request attempts** and no settled tile gaps. Six initial local requests are
cancelled during reframing. Services are stopped and reaped. Minor friction is
scrolling to share controls and the sandbox's initial loopback restriction.
This is AI-user evidence, not a human usability study or performance benchmark.

Reports, screenshots, commands, request events and artifact hashes are retained
in ignored `build/first-use/offline-independent-agent/` and
`build/first-use/offline-verification.json`. Earlier zoom failures and the
CSP-only candidate's blocked logo requests remain separately recorded.
Human participants, other platforms/browsers, hosted CI and cloud deployments
remain unqualified by these checks.

## Indexed virtual mosaics and broad-view snapshots

Local Linux x86_64 qualification on 2026-10-07 uses the installed CPython 3.12
wheel `rcfp-0.1.1.dev11+gc61190725.d20261007-cp312-abi3-linux_x86_64.whl`.
Its SHA-256 is
`90029b5a53fd8b38edb80723553c8dbfcfbf73ab0604565258d50dc9897e2d57`.
The executable is
`b88bceaf68d11329f44f07cc1913bf8c98cbaf9213d104c7d909ae96d7ce4e87`
and shared Rust core is
`b6e44780aa6853e9300ab58d2eb72ef20da8d7e8fee7b52948619985f758f8da`.
Installed executable/core bytes match the frozen bundle. The Python extension
matches the wheel, and the source archive retains the helper, runner, guide and
Hatch VCS requirements.

All **20 mosaic cases** pass. They cover indexed footprint and exact attribute
selection, deterministic per-channel overlap and ownership, mixed CRS and grids,
seams, masks/nodata, empty selections, source generation changes, and rejection
of every budget before raster submissions. A 100-file collection exercises
bounded window preparation. Independent Rasterio/NumPy expectations verify
samples and decoded viewport pixels. Product checks verify expression evaluation
before overlap and aggregation, reproducible output, parent/definition/output
freshness, failed preparation cleanup, and explicit nearest category handling.
The documented prepare/check/register/render CLI sequence also passes.

Four requests per viewport select **1, 2, 1, 2** sources for detailed views and
**1, 1, 1, 1** for broad products. Measured reader calls equal those plans: six
and four. Local viewport completion is **24.52/9.01 ms** for detailed/broad
views, with sampled peak service RSS of **63.14/63.75 MB**. An instrumented
loopback range origin with 2 ms request delay records **33.33/17.84 ms**,
**120,184/7,240 source bytes**, and **67.20/67.47 MB** sampled peak RSS.
All 16 responses are HTTP 200. Reports retain HEAD/GET/range counts, response
bytes, source-byte estimates and backend counters. These are observations from
one workload with two workers, disabled sample/render retention, a shared
service and warm filesystem. They are not comparative performance claims or
hard RSS/network bounds. Broad products remain explicitly selected immutable
layers with operator freshness checks and regeneration.

The same artifacts pass **205 contract cases** in total: 20 mosaics, 51 advanced
layers, 23 catalogs, 53 public contracts, 14 compatibility contracts, 26 pinned
Terracotta differential cases and 18 profile cases. Rust passes **61 tests**.
Two fixture-dependent TIFF tests and one image timing benchmark are separately
ignored. Clippy with warnings denied, changed-file Rust formatting, Ruff,
archive checks and strict Sphinx pass. Sphinx builds **61 pages** and checks
**6,061 local links**. The product scan only matches the intentional obsolete
package exclusion assertion in the archive checker.

Artifact and frozen-source hashes, report hashes, measurements, logs and
superseded failures are recorded in ignored
`build/mosaic-validation/verification.json`. The snapshot includes concurrent
workspace changes present at capture. Later unrelated changes are outside this
artifact qualification, with a separate current-workspace all-target Cargo
check passing. Input derivatives retain checksum-pinned real pixels with
declared Mercator crops, decimation, masks and shifted placement. Reused date
labels test selection, not acquisition history. This unrepaired local wheel does
not qualify other platforms/Python versions, hosted CI, cloud/WAN access,
physically cold storage or browser paint. Prepared Linux CI is not execution
evidence.

## Python-hosted scalar compute layers

Local Linux x86_64 qualification on 2026-10-07 uses the repaired
`cp312-abi3-manylinux_2_39_x86_64` wheel. Its SHA-256 is
`36c07ac993c8030eec250da2d68b87e879d61db9909a6ac0bf63439c13aaf73a`.
Independent installations pass **21 compute cases on CPython 3.12** and **20
each on 3.13 and 3.14**. The additional 3.12 case displays discovered models
through the regular viewer and existing Leaflet, MapLibre and OpenLayers clients.
Browser execution is separately skipped on 3.13 and 3.14.

The runner uses checksum-pinned real Sentinel pixels and records derivatives
with calibration, independent validity, decimation and explicit Float64 casts.
Independent Rasterio expectations verify aligned inputs and decoded model
images. Cases cover coherent observation selection, physical band names,
raw/scaled and categorical inputs, immutable context/buffer lifetime, malformed
and nonfinite outputs, sanitized failures, parameters, source/model/dependency
revisions, style-only reuse and independent bounded cache budgets. Loopback
range-origin tests exercise unrevisioned remote retention and immutable
revision-addressed inputs. Concurrent tests cover callback sharing, saturation,
expired queued work, running deadlines, source mutation and graceful shutdown,
including 503 responses for callers whose queued work is removed during close.
Both a maintained WSGI host and the published satellite/weather host example
serve concurrent requests using installed public APIs.

Publication checks verify configured HTTPS/prefix URLs, invalid origins and Host
headers, and forwarded-header isolation through the shared native URL builder.
An installed host mounted at `/imagery` serves discovery, TileJSON and a visible
tile by following the published URLs over loopback HTTP.

The final wheel also passes **53 native public contracts**, **15 compatibility
contracts**, **51 advanced-layer**, **54 native-service** and **26 pinned
differential cases per profile**. Its byte-image API passes in a separate
installation without NumPy, Rasterio or Pillow. Rust passes **61 tests**, with two
fixture-dependent TIFF tests and one image timing benchmark separately ignored. Clippy with warnings
denied, Rust formatting, Ruff, **13 SDK tests**, strict TypeScript compilation,
installed reference extraction, wheel/archive checks and strict Sphinx pass.
Sphinx builds **65 pages** and checks **6,759 local links**. The source archive
preserves Hatch VCS configuration and the compute guide, example and runner.

Reports, artifact/source hashes, screenshots, logs and separately retained
superseded failures are in ignored `build/python-compute/`. This qualifies one
local Linux platform and loopback origin. Other platforms, hosted CI, deployed
hosts, WAN/provider freshness, GPU/subprocess/async callbacks and multiprocess
cache sharing remain unexecuted. Spatial padding and multiple-observation
compositing are outside the scalar contract. The example's weather formula
illustrates integration and has no agronomic qualification. Prepared CI is not
execution evidence.
