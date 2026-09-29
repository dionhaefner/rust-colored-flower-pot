# RCFP rewrite: guiding policy

This document guides implementation and review of the Rust rewrite. Read it
before choosing architecture, changing behavior, or deciding whether a mismatch
needs more investigation. Keep it current when project decisions change;
implementation details belong with the code, and discoveries belong in
[REWRITE_LOG.md](REWRITE_LOG.md).

## Objective

Deliver a Rust implementation of Terracotta with full feature coverage and
practical drop-in replaceability. Existing users should be able to keep their
rasters, databases, URLs, clients, and ingestion workflows. Preserve Terracotta's
ease of exploration, minimal assumptions about user data, self-describing API,
and support for serverless deployment.

Use the rewrite to improve deployment, resource use, maintainability, and
performance where evidence supports it. Rust alone is not evidence of an
improvement. Shipping a reliable replacement takes priority over an ambitious
redesign or theoretical speedup.

## Compatibility policy

**The initial port reproduces existing observable behavior as much as feasible.
For rendering, the goal is what a person can see in a real Terracotta app, not
bit-for-bit numerical reproduction. Proven, visually immaterial differences
such as rounding are acceptable. Do not chase those rabbit holes.**

This visual standard applies to numerical and rendering fidelity. It does not
excuse broken API contracts, incorrect dataset selection, unreadable databases,
changed categorical meaning, or workflows that no longer run.

| Surface | Required fidelity |
| --- | --- |
| HTTP and client behavior | Preserve routes, methods, query semantics, defaults, response structure, meaningful status codes, headers, CORS, and version negotiation. Preserve error meaning; exact wording matters where consumers depend on it. |
| Images | Preserve appearance, location, seams, coverage, transparency, categorical meaning, dimensions, stretches, and color operations at ordinary application viewing scales. Compressed PNG bytes need not match. |
| Numerical results | Preserve the algorithm's meaning and validity/mask behavior. Small explained differences are acceptable when they do not materially affect displayed imagery, metadata use, or downstream behavior. |
| Storage | Read existing databases and raster paths correctly. Preserve metadata encoding and supported write/read workflows, including rollback to the Python implementation for unchanged database formats. |
| CLI and Python | Preserve commands, important flags, configuration precedence, public call semantics, return types, exceptions, and integration workflows. Terminal progress formatting and implementation internals need not be identical. |
| Deployment | Preserve supported use cases. A changed installation or launch procedure must be explicit; native service support alone does not satisfy Python/WSGI compatibility. |

**User-directed package identity exception (2026-09-28):** This project
installs the `rcfp` distribution, exposes `import rcfp`, and registers the
`rcfp` command. It does not install a `terracotta` Python import shim. Existing
Python applications must change their import and command names when switching.
The legacy database encoding, protocol version, routes, and raster behavior
remain compatibility targets. The API document title is RCFP. This accepted
namespace difference is recorded as RW-052/D-016.

Existing behavior is the reference, including inconvenient quirks. Do not use the
port as an opportunity to silently fix old bugs, rename interfaces, introduce a
new database schema, or improve the output aesthetically. Record those changes
as follow-up work. Safe internal improvements are welcome when they preserve
the observable contract.

When a mismatch appears:

1. Reproduce it with a small, retained input and equivalent settings. Check
   dependency versions and defaults before attributing it to Rust.
2. Classify the impact: integration failure, visibly different rendering,
   materially different data semantics, or an immaterial numerical/encoding
   difference.
3. Fix integration failures and material differences within the initial port.
   If compatibility is infeasible, document the specific limitation and its
   user impact; do not redefine the feature as complete.
4. For an explained immaterial difference, record the evidence and a suitable
   regression bound in the log, then move on. Do not spend days reproducing a
   rounding artifact invisible in normal use.

Use side-by-side images at realistic map zooms and display sizes, backed by
numeric comparisons. Amplified difference images help diagnose problems but
are not themselves evidence of a user-visible regression. A global error score
must not hide a narrow tile seam, a shifted boundary, or changed category labels.
Do not introduce a universal pixel tolerance that makes unexplained failures
pass. Routine acceptance of an evidenced, immaterial difference is already
authorized by this policy.

## Scope of the initial port

An early milestone may support only part of this list. The complete replacement
must account for every surface; unsupported items remain visible work.

| Area | Coverage |
| --- | --- |
| Discovery and metadata | Keys; dataset filtering, ordering, and pagination; individual and batch metadata; column selection; colormap discovery; API specification and documentation endpoints. |
| Rendering | Singleband, RGB, and compute tiles and previews; existing reprojection/resampling choices; explicit categorical maps; built-in and custom colormaps; percentile stretches; color-transform language; tile dimensions; empty tiles. |
| Raster workflows | Local and supported remote rasters; metadata calculation; sampled/lazy and chunked statistics; coverage geometry; raster optimization, masks, compression, and overviews. |
| Metadata backends | SQLite, S3-hosted read-only SQLite, MySQL, PostgreSQL, supported migration paths, dynamic dataset keys, and stored raster paths. |
| CLI | `serve` from a database or raster pattern, `ingest`, `optimize-rasters`, `connect`, `migrate`, and configuration through flags, TOML, and environment variables. |
| Python integration | Public settings and driver APIs, connection contexts, ingestion and retrieval, errors, masked arrays, asynchronous results, and documented Flask/WSGI integration. |
| Client and operations | Browser exploration, containers, Lambda, credentials, custom endpoints, caches, logging, profiling/tracing behavior, and the supported operating-system matrix. |

Freeze the reference commit and dependency environment before building the
compatibility corpus. The initial assessment used commit `6db38d5`; this is a
starting reference, not a claim that an executable baseline has already been
captured. Incorporate later reference changes deliberately.

## Starting architecture and trade-offs

These are the default direction. Revise them when experiments justify a change,
recording the evidence and compatibility consequences here.

- **Keep GDAL initially.** Rewrite application logic in Rust while retaining the
  raster/projection machinery. Match Rasterio's orchestration and options, not
  just the underlying library name. Isolate any low-level bindings behind a
  small ownership-safe interface. A GDAL-free backend is a separate future
  project, not a prerequisite for this port.
- **Use one core through several adapters.** HTTP, CLI, Lambda, and Python must
  share rendering and metadata logic. Keep transport validation and legacy
  representation details at their boundaries. Prefer a small number of clear
  modules to a speculative plugin framework.
- **Keep Python compatibility explicit.** A Rust executable does not replace
  `import terracotta`, a Flask app factory, or a WSGI entry point. Provide thin
  compatibility bindings/adapters and test their public behavior. Keep Python
  out of the native server's runtime dependency path where feasible.
- **Reuse the browser client initially.** Serve its existing assets from the new
  application. Rust/Wasm conversion and interface redesign are separate work.
- **Preserve storage layouts.** Retain backend-specific behavior and the existing
  encoding. Keep implementation version, legacy protocol compatibility version,
  and database format conceptually separate so a new release number does not
  strand existing clients or databases.
- **Bound raster execution.** Blocking raster work belongs outside async request
  execution, with bounded workers, queues, and memory use. Start with
  worker-owned GDAL dataset/VRT handles. Do not assume shared handles are safe
  or that cancelling an HTTP request stops native work.
- **Package early.** Keeping GDAL means shipping native libraries, codecs,
  projection data, and certificates. Exercise container/Lambda and desktop
  distribution before treating deployment simplicity as achieved.

Web frameworks, database libraries, cache policies, and crate boundaries are
implementation choices, not project goals. Prefer established components;
prototype the difficult integration before committing to a dependency stack.

## Pitfalls to keep in view

### Raster and image behavior

The current raster path uses suggested resolution, a resolution fallback,
padded warped VRTs, a separate read/resampling step, and separate mask assembly.
Start by preserving that sequence. Reprojection and resampling are independent
settings; categorical rendering forces nearest-neighbor behavior.

Test tile boundaries, overview selection, very high/low zooms, non-square
outputs, high latitudes, antimeridian cases, unusual projections, nodata, NaNs,
internal masks, and alpha. Cover both tiles and whole-dataset previews. A valid
PNG of the right size is insufficient evidence of correctness.

Pixel scaling reserves zero for transparency and uses NumPy rounding. RGB
transparency, explicit palettes, and the custom `.npy` colormap format are part
of existing behavior. Prefer reusing the supplied palette values to regenerating
approximately similar colors. Minor rounding differences follow the visual
policy above; palette shifts, category changes, and transparency regressions do
not.

### Expressions and metadata

The compute endpoint exposes a restricted Python/NumPy expression language.
Match precedence, scalar/array behavior, arithmetic, comparisons, functions,
and mask propagation. A generic expression crate is not automatically a
compatible evaluator. Preserve color-operation ordering and channel semantics
as well. Bound parsing/evaluation work without quietly rejecting ordinary
supported expressions.

Whole-image, sampled/lazy, and chunked metadata paths have different accuracy
characteristics. Preserve their meaning; another quantile estimator need not
produce identical numbers. Test their impact on stretches and displayed
coverage. Do not substitute an unrelated statistical definition merely because
rendered tiles happen to look similar on one fixture.

### Storage and compatibility

Beware of misleading bounds column names, raw float32 percentile blobs, key
ordering, SQL collations, path normalization, overwrite behavior, and transaction
boundaries. Validate both Python-to-Rust and Rust-to-Python database use.

Metadata reads can trigger computation and writes. Concurrent lazy loads need
coordination, and read-only backends must retain their failure semantics. Remote
SQLite uses whole-file downloads with a refresh interval; safe replacement must
account for live connections and in-flight requests.

Existing clients check the version in `/swagger.json`, and database validation
uses application major/minor versions. Preserve those legacy expectations while
exposing the rewrite's actual implementation version separately.

### Resource use and performance

Account for all concurrent memory: raw bands, masks, expression temporaries,
encoded images, application caches, GDAL caches, and open datasets. Avoid
multiplying request, band, and native-library parallelism. Cache identities must
include every option that changes output, with deliberate handling of replaced
rasters and refreshed metadata.

Keep remote range reads, credentials, custom S3 endpoints, credential refresh,
and retries in the test matrix. Adding a Rust object-storage client does not
automatically change how GDAL fetches raster data.

Benchmark against a sensibly configured production Python deployment at equal
resource budgets. Record cold/warm behavior, concurrency, latency distributions,
throughput, peak memory, remote requests/bytes, startup, and artifact size.
Existing native dependencies already do substantial work; do not promise a
speedup based on eliminating Python alone.

## Validation and delivery

Build a differential harness before a broad port. Reuse the existing tests and
fixtures, but add comparisons where tests only check status or shape. Keep a
reproducible corpus and dependency/version manifests so differences can be
reproduced. Acquire a small set of real community raster datasets in the first
phase. Prefer native-resolution subsets of recognizable imagery over synthetic
scenes, retaining source characteristics and provenance. Keep a separate large
raster tier so cropping does not remove the resource and file-size challenges.
Use synthetic microfixtures only for isolated edge cases that real subsets cannot
exercise reliably. The corpus plan is in [process/execution/DATASETS.md](execution/DATASETS.md).

Compare HTTP behavior, arrays and masks, decoded images, metadata, CLI flows,
Python calls, and database round trips. Keep byte-level comparisons only where
bytes are contractual, such as storage encoding; PNG compression streams do not
need to match. Accepted deviations should have narrow, meaningful checks that
still catch material regressions.

Deliver vertical slices:

1. Freeze the baseline, acquire the real raster corpus, map features, and capture
   reference results, visual examples, and timings.
2. Read an existing SQLite database and reproduce a singleband tile and preview.
3. Add remote reads, RGB, bounded concurrency, and resource measurements.
4. Prove packaged deployment, a Python binding, and database round trips early.
5. Complete remaining endpoints, expressions, CLI workflows, backends, adapters,
   and distribution targets against the same compatibility harness.
6. Exercise the existing browser client and ingestion workflows end to end,
   including a return to the Python implementation on the same compatible data.

The initial replacement is complete when the scope inventory is covered,
material compatibility failures are resolved, accepted differences are explained
and logged, existing workflows run, and distribution/resource behavior has been
validated. Publish measured benefits and limitations. Do not label a subset of
the server as full parity.

## Working discipline for agents

- Read the relevant Python implementation and tests before porting a feature.
  Documentation is evidence of intent; executable behavior reveals quirks.
- Check the discovery log before investigating a known difference again.
- Keep each change focused on a feature or an evidenced architectural need.
- Record findings as they arise, including suspected problems that remain
  unconfirmed. Distinguish inspection, reproduced behavior, and hypotheses.
- Fix regressions introduced by the rewrite now. Defer legacy bug fixes and
  feature improvements to subsequent versions unless scope is explicitly changed
  or the issue prevents delivering the port. Record any necessary exception.
- Record visually immaterial, explained deviations and move on. They do not
  require a separate permission cycle, nor are they necessarily future bugs.
- In handoffs, state what works, how it was checked, remaining limitations, and
  relevant log IDs. Never claim parity or performance without evidence.
