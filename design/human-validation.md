# Validation handoff

This document records completed local checks, raster correctness results,
and the remaining platform, service, release, and conditional support
qualification. Local evidence was collected on **2026-10-05**. Results establish
only the recorded source and artifacts; rerun affected checks after changes.
Configured CI, a completed execution, and a passing qualification are separate
statuses. The corrected broader raster matrix passes **all 3,646 cases** on the
recorded Linux x86_64 shared core; hosted platform qualification remains pending.

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
      version; 26 exact decoded-image HTTP comparisons in each profile and 28
      selected public reference replay cases.
- [x] Render admission corrected to configured worker-plus-queue capacity;
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
one/two with exact imagery; it does not establish cold/WAN or production gains.

The pinned oracle baseline had 526 passes and two classified ANSI failures out
of 528 cases, with six remote-SQLite mock cases excluded. These are reference
classifications, not RCFP product passes. The 28-case public replay selects
supported workflows; live SQL checks have a separate scope.

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

The general reader now suggests resolution from an axis-aligned proxy of the
source bounding rectangle, matching the reference's bounds-based calculation
for rotated grids. Its padded VRT receives the affine directly through
`GDALCreateWarpedVRT`; reconstructing resolution from padded bounds through the
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
Wheel contents/RECORD/metadata and strict repair checks pass; wheel SHA-256:
`260360f56a0a953c77349eb152290fd662b4c3661c19bca04bc1a08b8027e8d0`.
Two socket tests were initially blocked by the sandbox; the complete unit suite
passed with loopback access. Earlier platform/resource/service results above
qualify their own artifacts and are not reruns of those scopes on this core.

These checks need no human account access. Retain an existing verified
`.cache/reference` and `.venv-reference`; otherwise prepare CPython 3.12 and the
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
checksum-pinned sources; omit it when the cache is complete. The default oracle
archive is `.cache/reference/source.tar.gz`; this local run used
`--reference-archive build/reference-acquisition/source.tar.gz`.
[Reference procedures](../tests/reference/README.md#broader-raster-correctness)
describe the comparison rules. The correctness workflow now includes this
matrix and retains its reports; that configured lane has not been executed on
the hosted reviewed revision. Other OS/architecture wheel cells and opt-in
samplers still need their separate qualification.

## Other pending qualification

The following scopes still need fresh evidence. Account/platform work requires
the intended environment; repository test extensions can be performed locally.
Performance measurements are needed before expanding performance claims, and
cross-endian work is conditional on proposing that support extension.

| Remaining scope                           | How to execute and what to retain                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hosted platforms and correctness          | Make the reviewed tree available on an authorized branch. Run `gh workflow run platforms.yml --ref BRANCH`, `wheels.yml`, `correctness.yml`, and `docs.yml` the same way. Inspect Linux x86_64/aarch64, macOS x86_64/arm64, and Windows x86_64 native targets and all 15 CPython 3.12–3.14 wheel cells. Verify Windows Cargo artifacts before installing the Python Rustup helper, strict Linux glibc/ISA repair tags, coverage, reference baseline, browser, physical BigTIFF ranges beyond 4 GiB and the full 180-second resource job. Review separate Rust/Python coverage before choosing thresholds. The correctness workflow now includes the full raster boundary matrix; inspect its execution and retained report alongside the other gates. Download artifacts with `gh run download RUN_ID --dir build/hosted-RUN_ID`; retain revision and artifact hashes. A dispatch of an older branch does not test this working tree. |
| Intended SQL TLS                          | Configure disposable MySQL/PostgreSQL servers with the intended private/public CA and server identity. Set `RCFP_TEST_MYSQL_URL` and `RCFP_TEST_POSTGRESQL_URL` with `sslmode=verify-full` and `sslrootcert` where needed; run the Rust storage integration and native catalog suite. On both native and Python paths, repeat valid CA, missing/untrusted CA, wrong hostname, and literal-loopback `sslmode=disable`, in fresh processes and with warmed driver reuse. Confirm negotiated TLS server-side; drop the test databases. Local MariaDB is not a hosted MySQL result.                                                                                                                                                                                                                                                                                                                                                       |
| Real S3 and credentials                   | In an authorized test prefix, use a pinned real COG and a consistent SQLite backup; follow [remote-data procedures](../docs/how-to/remote-data.md). Confirm raster range reads, read-only catalog writes failing, generation replacement with an old outer Python reader pinned, and failed-refresh recovery. Repeat with the intended custom endpoint, session token, role refresh and SSO renewal. Retain redacted request/signing/refresh evidence and delete test objects.                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Lambda, both architectures                | Run the native platform workflow's x86_64/aarch64 AL2023 image/build/runtime lane. The local qualification environment had no Docker/Podman. Then deploy those exact bundles through the authorized infrastructure workflow, as described in [Lambda procedures](../docs/how-to/lambda.md). Invoke `/keys` and image events through the actual API Gateway integration; decode PNG/base64, repeat warm/cold/error invocations, check shutdown and credentials, and confirm a sampled trace arrives in X-Ray. Retain hashes, configuration and results, then remove disposable functions/roles/objects.                                                                                                                                                                                                                                                                                                                                |
| External basemap/deployment               | Start the bundled client against the intended backend and real basemap/token. Select a dataset; exercise singleband, RGB, metadata, missing-data/error handling, navigation and CORS. Retain screenshots, decoded raster results and browser/network errors. The local Chromium run used an offline basemap.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Read the Docs and human trial             | Connect the repository in the owner account, build the reviewed revision and release versions using `.readthedocs.yaml`, activate versions, and set the actual canonical URL. Retain hosted build logs and verify navigation/search/mobile pages. Have an independent user follow installation/first-map and Terracotta migration/backup/rollback instructions and record issues. Local Sphinx, examples, browser and external links have already run.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Exact release and publication setup       | After the raster matrix and required gates pass, build at the intended `vMAJOR.MINOR.PATCH` tag. Check all five wheels and the sdist with `python packaging/verify-release.py DIST vMAJOR.MINOR.PATCH`, plus strict Twine checks. Configure protected main/tags, the `pypi` environment and PyPI trusted publisher. The tag-push workflow publishes: do not push a release tag until publication is authorized. Local development versions do not qualify the tag.                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Opt-in sampler correctness                | Add explicit opt-in variants to `tests/reference/raster_boundaries.py`; its current matrix disables fast samplers and prefetch. Exercise `TC_RUST_MERCATOR_FAST`, native `TC_RUST_UTM_FAST`, decoded-block caching and prefetch on eligible inputs plus fallback edges. Compare exact-path values/masks/images and use independent coverage, category and seam checks for intentional native sampling differences. Apply the input-specific UTM bounds in [compatibility notes](compatibility-notes.md#narrow-accepted-numerical-differences) only to the recorded EPSG:32632 zoom-13 UInt16 corpus; broader inputs need fresh evidence. Verify that compatibility mode disables UTM sampling even when requested. Retain exact core/wheel identities and per-case results before expanding support claims.                                                                                                                           |
| Cold/WAN/performance and longer resources | The local matched-cache warm benchmark and 180-second resource gate are complete. Before expanding claims, use disposable benchmark hosts with matched total cache budgets and CPU affinity to measure actual cold disk, WAN bytes, startup/first view, loaded p95/p99, production budgets, more cores and a longer soak. Run `python docs/tools/benchmark.py --reference-python .venv-reference/bin/python --out build/warm-review` for the maintained warm control. Preserve exact binaries, input hashes, workload and exclusions; do not reuse its warm ratios as WAN/production claims.                                                                                                                                                                                                                                                                                                                                          |
| Cross-endian support extension            | Only if cross-endian support is proposed: use an actual big-endian target or emulator with native dependencies. The endian-tagged format proposal was discarded; existing cross-endian catalogs remain unqualified. Create/read legacy float32 percentile blobs in each byte order and compare known stored percentiles and rendering in both directions. Record unsupported combinations explicitly. Byte-swapping on this little-endian host alone does not qualify cross-endian operation; a format change requires a separate decision.                                                                                                                                                                                                                                                                                                                                                                                           |

## Retained scope decisions

Preserve the current catalog layout and image mask/transparency contracts.
Endian-tagged percentile encoding and a broad transparency redesign were
discarded; a future portable format needs an explicit marker and migration.
Legacy cross-endian catalogs remain unqualified. Native percentile label and
rotated metadata-hull corrections change behavior within the existing format;
public differences are described in [native migration](../docs/native-migration.md).

Speculative expression rewrites and repeat singleflight/reusable-transformer
trials are not outstanding implementation tasks. Retain bounded caching/prefetch
and the GDAL fallback; reconsider optimizations with fresh measurements and a
concrete requirement. Broader hosted raster qualification and opt-in sampler qualification
remain the explicit tasks above.

## Evidence retention and completion

| Retained local directory                     | Scope                                                                                                                                                                                                                                                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `build/validation-20261005/`                 | Earlier Linux artifacts: SQL/storage, remote ranges, reference baseline, browser, physical BigTIFF, documentation, coverage and warm control; original failures and setup corrections remain separate.                                                                                            |
| `build/blocker-fixes-20261005/`              | Fixed native bundle/repaired wheel: admission, SQL cache/TLS, metadata/build gates, contracts and reference replay. Native SHA-256 `7dd4e8c88e400471c6c14bcc7990599702e0acf63b3b932733abc655d672c805`; repaired-wheel SHA-256 `c56620feb6287a1399d72a0c5c81e96bb83f5638aaaa2e0c74be4104b6f395f4`. |
| `build/raster-correctness-20261005-current/` | Original frozen core and the full matrix with 358 failures, retained as negative evidence.                                                                                                                                                                                                        |
| `build/raster-fixes-20261005/`               | Corrected frozen core, full passing matrix, original-vs-fixed reproductions, rebuilt wheel and fresh interface/unit checks; initial sandbox and wheel-tool setup failures are retained separately.                                                                                                |

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
See [performance](performance.md) for each ranked knob's implemented/discarded
disposition, measured local/range gains, actual S3 status and defaults.

Native direct tile sampling is enabled by default. The independent 193-check
matrix passed each of nearest/bilinear/cubic/average, including real polar/
dateline/rotated/unaligned-Mercator/northern/southern sources, nodata/alpha, overview/edge/rectangular
outputs, categories, multiband RGB and explicit WebP coverage. Both profiles'
standalone contracts and seven local cache-freshness checks passed. The new path
completed the full 180-second eight-client/three-worker resource test with
152815 successful HTTP requests and no failures/overloads/timeouts. This is
uncached-render stress; it is not a production memory claim for a large resident
cache. Unit and Clippy checks passed separately.

These tile gates do not close the broader metadata/preview correctness matrix,
fresh release/wheel/platform/browser/cold-storage gates, private provider TLS or
credential/mutable-object qualification above. Earlier frozen failed matrix
results describe their original artifact. Native exact Terracotta pixel controls
must disable direct sampling; the direct path has a separate native quality
contract. Keep failed S3 and contaminated/incomplete timing runs separate from
successful paired measurements, as described in the performance document.

The local installed wheel passed 14 adapter/CLI/WSGI contracts and a nonempty
native RGB/defaults/format check. Sphinx and 4159 local links passed. Raw-wheel
verification and portable manylinux_2_34 repair failed; this host requires
manylinux_2_39 symbols. This is a portability limitation, not a local API pass
or a release-qualified artifact. Fresh Terracotta differential replay remains
unavailable in this environment.

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
counts; this successful-request check does not test cancellation during GDAL.

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

The common-workload comparison addresses part of issue #8, without qualifying
actual S3, physically cold disk, browser/WAN, true time-series acquisitions,
packed multiband/mosaics, cross-service deadline/disconnect drain or release
platforms. The measured RCFP profile is native; no fresh exact Terracotta-profile
differential replay is implied by these display-workload timings.
