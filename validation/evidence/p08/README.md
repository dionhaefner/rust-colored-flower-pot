# P08 release qualification evidence

The final local candidate is qualified on Linux x86_64. Full release remains blocked
on actual AWS services, deferred platforms and the external basemap workflow;
these are not accepted numerical differences. See [release notes](../../../packaging/RELEASE_NOTES.md),
[rollback instructions](../../../packaging/ROLLBACK.md), [plan](../../../execution/plans/P08.md)
and the [phase ledger](../../../execution/STATUS.md).

## Provenance and interpretation

- The frozen oracle is commit `6db38d5873cde5ebabe47338a1f61ddc788eada6`.
  [Integrity check](oracle-integrity.json): all 412 tracked blobs match the git
  objects at that commit. The sibling checkout's mutable tree was not used.
- The P00 pinned real corpus and GDAL 3.9.3 remain the reference. P08 adds three
  anonymous Copernicus DEM assets totaling about 13 MB, pinned in
  [geography-assets.json](geography-assets.json), with an explicit offline/
  download [runner](../../tools/p08_acquire.py). Crops retain native values and
  resolution. The dateline mosaic concatenates adjacent E179/W180 source columns,
  unwrapping longitude by +360 without resampling or invented pixels.
- [Qualification commands](qualification.json) preserve actual exit codes,
  elapsed times and final outcomes; earlier failed logs remain separately retained. Accepted
  job completion is separate from a passing test count. No skipped/excluded
  case is counted as a pass. Test counts overlap and must not be summed.
- Final artifacts are `validation/artifacts/p08-final`, `p08-final-python`,
  `p08-final-wheels`, and `p08-final-lambda`. Earlier P07 binaries and initial P08
  results precede one or both repairs and do not qualify the final candidate.
- [Performance](PERFORMANCE.md) retains the full three-trial setup, per-workload
  distributions, throughput, startup, service-tree RSS/handles and range traffic.
  Results are mixed; no universal speedup, WAN or cold-disk claim is made.

## Final local results

Counts overlap; these are separate checks, not an additive total.

| Surface | Result | Evidence |
| --- | --- | --- |
| Rust | 20 unit tests, SQL and remote integration tests, fmt and Clippy pass | [commands](qualification.json) |
| HTTP / packaged adapters | 526 / 46 checks pass | [HTTP](http.json), [adapters](adapters.json) |
| Unchanged applicable Python suite | 526 pass, 2 known oracle ANSI failures, zero skips; 6 boto-mock cases excluded explicitly | [XML](upstream-final.xml) |
| Public integration / settings / remote emulator | 27 / 46 / 13 pass | [integration](integration.json), [settings](settings.json), [remote](remote.json) |
| Preparation / real SQL engines / migrations | 329 / 123 / 19 pass | [preparation](preparation.json), [engines](engines.json), [migrations](migrations.json) |
| Rollback | 5 workflows pass, including old Python reading native writes | [rollback](rollback.json) |
| Geographic rendering | 30 polar/dateline images exact, including three mosaic preview routes | [geography](geography.json), [comparison sheet](images/p08-geography-final/side-by-side.png) |
| Physical BigTIFF | 9 checks; 7,109,429,559 bytes, 5,041 blocks and masks exact, real ranges beyond 4 GiB | [BigTIFF](bigtiff.json) |
| Lifecycle and overload | 20 owner lifetimes, TCP disconnect/drain, 503 overload/504 timeout and recovery pass | [resources](resources.json), [failure](failure.json) |
| Soak | 180 seconds, 12 clients; 10,384 successful and 38,384 expected overloaded responses; peak RSS 139 MiB, final FDs return to 6 | [soak](soak.json) |
| Cold process shutdown | 200/200 exact renders and clean exits inside pinned official AL2023 image | [shutdown](shutdown.json) |
| Installation/runtime | Fresh wheel, Debian and official AL2023 exact rendering pass | [install](install.json), [container](container.json), [Lambda image](lambda-runtime.json) |
| Browser | Chromium singleband/RGB/metadata exploration; queue 64 and every backend response checked | [browser](browser-qualified/report.json) |
| Performance | 24 matched runs, three trials per engine/access/cache configuration, exact decoded responses | [measurements](PERFORMANCE.md) |
| Detailed source inventory | All 386 records reconciled with explicit external blockers | [audit](coverage-reconciliation.json) |

## Findings and repaired failures

**RW-039:** the old P07 Lambda zip exited 139 on the initial official AL2023
invocation and two of 30 repetitions ([old repeats](lambda-repeat.json)). The
failed repeats produced image JSON before crashing at process exit. Forty host
debugger runs did not reproduce it; no backtrace is claimed. Inspection found
detached GDAL workers. The repair retains JoinHandles, closes admission, drains
accepted work and waits for worker/thread-local cleanup, including partial
startup failure. The Rust regression checks both queued work and actual
thread-local destruction. Persistent response deadlines still do not cancel
GDAL; destroying an application waits for its admitted work (D-011).

**RW-040:** 94 detailed inventory rows retained stale not-started states despite
P05/P06 implementation. Reconciliation links the actual source, prior evidence,
related unchanged upstream outcomes and explicit external boundaries. Constants
and internal type variables are inspection records, not fictitious runtime tests.

P08 harness corrections preserve their failed evidence: palette PNG contact
sheets need explicit RGBA conversion; `/proc` sampling stops before child
termination; the Python oracle's BigTIFF HTTP server runs in a separate process
to avoid a GDAL/GIL stall. The rollback rehearsal originally expected `0.10.1`
after historical migration, while both implementations write `0.10.0`; the fixed
test executes the frozen migration and compares versions, keys, datasets and all
stored metadata. Its initial incorrect CLI-module invocation was also repaired.
Neither issue required changing application migration behavior or the oracle.

**RW-041:** the actual dateline-spanning mosaic exposed a material preview
framing error, then two extra opaque edge columns. HTTP now leaves preview bounds
to the source raster transform, and the warped VRT explicitly disables GDAL's
automatic center-longitude insertion to match Rasterio. The final geographic
matrix checks singleband, RGB and compute previews plus polar/dateline tiles
exactly. Retained failures: [initial framing](images/dateline-framing-before/),
[intermediate mask mismatch](images/dateline-mask-before/),
[Python VRT](dateline-python.vrt), [native VRT probe](dateline-native-probe.log),
and [pinned GDAL option source](gdal-transformer-source.txt). The near-global,
transparent whole-mosaic preview is a preserved oracle quirk, not an aesthetic
improvement. Pre-fix benchmark/artifact evidence is kept under `before-dateline`.

Browser review found one 503 in the default eight-job queue during the client's
tile burst, despite an intact screenshot. That run remains in `browser-final`;
`browser-qualified` uses the documented 64-job queue and asserts every backend
response succeeds. Worker concurrency remains bounded. This is an operator
configuration choice; no default or client behavior was silently changed.

The two unchanged upstream ANSI capture failures remain RW-036. The six
in-process boto-mock cases are explicitly excluded (RW-037), with separately
labeled socket-emulator checks. No oracle failure, exclusion or emulator success
is presented as a real AWS pass.

## Reproduction and retention

Use [validation/README.md](../../README.md#p08-combined-release-qualification)
for the full build, acquisition, benchmark and qualification commands. Run the
benchmark separately from heavy validation. Start only the disposable SQL
engines before `p08_qualify.py`; stop them afterward. `--resume` preserves completed
job evidence and resumes at the next unresolved job after its failure is repaired.

The physical BigTIFF is retained in ignored `validation/artifacts/p08-bigtiff`.
Its exact uint8→uint32 widening writes every original WorldCover sample once,
with uncompressed 512×512 blocks and nearest overviews. The runner verifies all
5,041 base blocks and masks, physical allocation, TIFF offsets and real HTTP range
offsets beyond 4 GiB. It is derived TIFF organization, not publisher-native
BigTIFF. Compact reports and comparison PNGs are retained here; the reproducible
source assets and recipes reconstruct the large file without fake padding.

The deterministic candidate packager produces a native tarball, canonical
Lambda ZIP, platform wheel, source/evidence archive, manifest and SHA256SUMS.
Identical output hashes from two preparations qualify archive reproducibility;
they do not establish bit-for-bit reproducible native compilation. Artifact hashes
and native library/data fingerprints identify the actual tested build.

## External release gates

| Gate | Affected inventory | Missing condition / next action |
| --- | --- | --- |
| B08-PLATFORMS | C32 and all claims on other platforms | User deferred Linux/Lambda arm64, macOS Intel/arm64 and Windows x86_64 runs (D-010). Execute prepared GitHub Actions later and retain uploaded evidence. |
| B08-AWS | C12, C16, C19, C30, C31 | No authorized test account/deployment/spend scope was supplied for real S3/custom endpoint, role/SSO, Lambda and X-Ray delivery. Provide that scope and disposable resource identities, then execute actual-service checks. |
| B08-BASEMAP | C24/C33 external basemap only | Qualified browser uses an offline background. Supply the intended authorized Mapbox/token deployment and validate external basemap integration. |

Publisher-native alpha remains optional corpus depth; declared real-pixel alpha
derivatives cover the current alpha semantics. The soak's duration is reported
explicitly and does not qualify a multi-day production leak claim. No publication,
tag, push, merge, production mutation or cloud deployment is part of P08.
