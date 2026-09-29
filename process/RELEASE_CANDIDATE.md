# RCFP 0.1.0 — local review candidate

The replacement preserves protocol and database identity **0.10.1**. The native
service, legacy Python imports/CLI, Flask/WSGI adapter and Lambda event adapter
share the Rust raster/storage core and bundled GDAL 3.9.3. Existing SQLite,
MySQL and PostgreSQL catalogs remain usable by the frozen Python implementation.

This is a locally qualified review candidate, **not a full-parity release**.
P08 remains blocked on actual AWS service checks and deferred platform execution.
Nothing has been published, tagged, pushed or deployed.

## Qualified scope

- Linux x86_64 native bundle, glibc 2.34 or newer; CPython 3.12 Linux x86_64 wheel.
- Clean Debian container and official AL2023 runtime-image loading/rendering.
  A local image test does not qualify an actual Lambda deployment.
- Discovery, metadata, singleband/RGB/compute, palettes, expressions and color
  operations; native preparation, ingestion, optimization and legacy migrations.
- SQLite, disposable MySQL 8.4 and PostgreSQL 16 integrations, cross-language
  reads/writes and rollback. S3 snapshots are tested with a socket emulator.
- Real Sentinel-2, Copernicus DEM and WorldCover imagery, polar/dateline DEMs,
  masks/alpha derivatives, and a physically allocated 7.1 GB BigTIFF with real
  blocks beyond 4 GiB. The BigTIFF uses exact integer widening of the full real
  WorldCover image; it is a documented derivative, not a publisher BigTIFF.
- Unchanged browser exploration with a test-only offline basemap. External
  Mapbox/token integration is not qualified.

The [P08 evidence index](validation/evidence/p08/README.md) identifies final
artifacts, full test outcomes, measured performance conditions and release gates.
The [inventory](execution/COVERAGE.md) retains partial claims explicitly.

## Measured performance

The [three-trial benchmark](validation/evidence/p08/PERFORMANCE.md) compares
three-core native serving with three production Gunicorn workers on identical
real rasters and GDAL 3.9.3. Results vary by workload/cache mode: some tile paths
are faster, while cache-disabled RGB previews and geographic DEM previews are
slower. No general speedup is claimed.

Median readiness was 0.022 s native versus 0.410 s Python in this setup. Sampled
service-tree RSS peaks ranged 113–190 MiB native and 462–579 MiB Python; summing
RSS double-counts shared pages across Python processes and is not a PSS result.
These local measurements do not establish WAN latency, cold-disk performance,
cloud cold-start behavior or results on other hardware. Raw distributions,
throughput, handles, range traffic and trial variation are retained.

## P08 repairs

Repeated official-runtime invocations exposed intermittent process crashes in
the P07 artifact. Raster workers are now owned, drained and joined before their
application is destroyed (RW-039/D-011). This includes native thread-local cleanup
and partial startup failures. Request deadlines and disconnects still do not
cancel native GDAL work. Destroying an application waits for outstanding work;
reuse a persistent application for serving requests.

The real dateline mosaic exposed incorrect HTTP preview framing and two opaque
edge columns. Source-derived preview bounds and matching GDAL longitude wrapping
now preserve the oracle for singleband, RGB and compute (RW-041). Thirty new
polar/dateline comparisons and the complete HTTP matrix pass exactly.

## Compatibility details and limits

The documented RW-019 float32 expression and RW-024/RW-025/RW-042 moment/hull differences
remain narrowly bounded by retained comparisons. PNG compression bytes need not
match. Category values, masks, dataset identity and schemas remain contractual.

Legacy quirks remain: MySQL reflected Decimal default stretches can fail unless
explicit ranges are supplied (RW-028); its historical 0.7 migration SQL is
unsupported (RW-027). MySQL URL query suffix behavior remains RW-030. Two ANSI
capture assertions fail in both the frozen oracle and replacement (RW-036), and
in-process boto monkeypatch tests are replaced with separately labeled socket
emulator checks (RW-037). Neither failures nor exclusions are reported as passes.

Native profile/debug flags provide documented request timing/diagnostics rather
than a Python call profiler or interactive debugger. Python futures use spawn;
guard executable scripts or disable multiprocessing as documented in
[OPERATIONS.md](../packaging/OPERATIONS.md). Python outer connection contexts preserve the
legacy per-operation commits; use the explicit native batch API for atomic batches.

## Remaining release gates

| Gate | Missing condition | Resume action |
| --- | --- | --- |
| B08-PLATFORMS | Linux/Lambda arm64, macOS Intel/arm64 and Windows x86_64 CI results | Run the prepared Platform qualification workflow when the user resumes deferred platform execution; retain each uploaded result. |
| B08-AWS | Authorized account/resources/credentials for real S3, custom endpoint, role/SSO, actual Lambda invocation and X-Ray delivery | Supply the intended test account and explicit deployment/spend scope; validate signed remote raster and snapshot refresh, credential rotation, Lambda binary replies and trace ingestion on disposable resources. |
| B08-BASEMAP | External Mapbox token/network workflow | Exercise the unchanged client against the intended external basemap deployment; retain the browser result. Offline exploration remains the qualified local scope. |

No unsupported operating system, cloud credential path or service deployment is
cleared by local emulators or documentation. Publisher-native alpha acquisition
remains optional corpus depth; actual alpha semantics are covered by declared
real-pixel derivatives. The three-minute soak is not a multi-day leak study.

## Artifacts and reproduction

`process/tools/prepare-candidate.py --out process/validation/artifacts/NEW-candidate` creates
the native tarball, canonical Lambda ZIP, CPython wheel, source/evidence tarball,
manifest and SHA256SUMS from the final P08 build. Two independently prepared
directories must have identical archive hashes. This proves reproducible
packaging of identical inputs, not bit-for-bit reproducible compiler output.

Build prerequisites, pinned reference/corpus and commands are in
[validation README](validation/README.md). Keep the native library/data/
license directories together. Install the wheel into its own environment. Use the internal [migration rehearsal](ROLLBACK.md) when comparing with the Python release.

## Executable rollback rehearsal

From the repository root, with the pinned reference environment and real corpus:

```sh
PYTHONPATH=.cache/reference .venv-reference/bin/python process/validation/tools/p08_rollback.py
```

The runner checks native ingestion, backup, overwrite and deletion, Python
read-back and rendering, historical migration, and byte-exact backup restore.
Current backup and schema upgrade instructions are in the [operator guide](../packaging/OPERATIONS.md).
