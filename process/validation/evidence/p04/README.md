# P04 rendering and HTTP completion

Completed 2026-09-23 against the unchanged exported Python commit
`6db38d5873cde5ebabe47338a1f61ddc788eada6`. No sibling checkout edits, oracle
patches, cloud deployment, or published artifacts. Runtime and input provenance
remain pinned by the P00 environment/corpus manifests; `run-manifest.json` records
this phase's source/artifact identities and actual commands.

## Results and scope

| Check | Result | Evidence |
| --- | --- | --- |
| Full native HTTP differential | 526/526 pass | [HTTP report](http/report.json), [requests and oracle responses](http/reference.json) |
| Late boundary regression | 175/175 pass | [report](boundaries/report.json) |
| All 16 resampling/reprojection pairs, custom maps, CORS/settings, masks | 93/93 pass | [report](edges/report.json) |
| Packaged Python, WSGI, Lambda v1/v2 and concurrent pool startup | 46/46 pass | [report](adapters/report.json), [build](build.log), [log](adapters.log) |
| P01 singleband/discovery regression | 26/26 pass | [report](p01-regression/report.json) |
| P02 RGB, local/range reads, cache and bounded concurrency regression | Eight image cases exact; resource/traffic checks pass | [report](p02-regression/report.json) |
| Restricted expression arrays and masks | 361 oracle cases pass inside 15 Rust tests | [check log](check.log), [base vectors](../../reference/p04-expressions.json), [typed vectors](../../reference/p04-expression-types.json) |
| Frozen upstream server, expression, image, colormap tests | 152 passed, zero skipped | [log](upstream.log), [JUnit](upstream.xml) |
| Embedded palette/docs/static asset audit | 176/176 pass | [audit](../../reference/p04-assets.json), [log](assets.log) |
| Offline corpus, subsets, comparator, Rust tests | Pass | [log](check.log) |
| Locked offline release build, rustfmt, Clippy with warnings denied | Pass | [build](build.log), [lint](lint.log) |
| Unchanged old client `connect` handshake | Exit 0 against native `/keys` and `/swagger.json` | [record](http/old-client.json) |

Upstream tests establish the executable oracle; they are not counted as Rust
parity checks. HTTP compares status, content type, meaningful headers, successful
JSON/ordering and documentation text, meaningful error terms, dimensions and
exact decoded RGBA. It does not require byte-identical compressed PNGs or
incidental error wording/header ordering. The client runs with `TC_TESTING=1`
only to suppress its persistent browser server; the real version/key handshake
runs unchanged, and separate native requests verify rendering. Browser E2E is P08.

## Contract matrix

Every supported P04 route and option is represented below. Exact URLs, methods,
headers, bodies, settings and individual results are in the linked JSON reports;
source contract records are reconciled in `process/validation/reference/contracts.json`.
These are observable native equivalents, not a claim that every legacy Python
callable has been exported (P07).

| Coverage | Routes / options | Cases and evidence |
| --- | --- | --- |
| C01 | GET `/keys`, ordered names/descriptions | `route--keys`, P01 `keys` |
| C02 | GET `/datasets`; arbitrary key filters, literal/bracket lists, `limit`, `page`, `order_by`, `ascending` | `datasets-*`, repeated query; path ordering, invalid values, zero/empty pages |
| C03 | GET `/metadata/<keys>`; POST `/metadata` JSON `keys`; `columns`, batch maximum | `columns-*`, `metadata-*`, `batch-columns`; empty, duplicate, wrong shape/count, missing, unknown columns; configured maximum in edges |
| C04 | GET `/colormap`; `colormap`, `stretch_range`, `num_values` | All 171 named legend cases, grayscale/default, 0/1/2/255/300 values, equal/reversed/numeric-string ranges; custom legend in edges |
| C05 | GET `/singleband/<keys>/<z>/<x>/<y>.png`, `/preview.png`; `stretch_range`, `colormap`, `explicit_color_map`, `color_transform`, `tile_size` | P01 tile/seam/mask checks; `single-option-*`, `single-transform-*`, every built-in palette; nullable/percentile/numeric/reversed ranges and malformed sizes; high zoom boundaries |
| C06 | GET `/rgb[/<keys>]/<z>/<x>/<y>.png`, `/preview.png`; `r/g/b`, `r_range/g_range/b_range`, `color_transform`, `tile_size` | P02 tiles, repeated bands, nonsquare and partial masks; `rgb-*` transforms, independent ranges, missing channels, invalid/nullable ranges |
| C07 | GET `/compute[/<keys>]/<z>/<x>/<y>.png`, `/preview.png`; `expression`, `v1`–`v5`, `stretch_range`, `colormap`, `tile_size` | `compute-*` images, all five inputs, invalid/missing inputs, float32 DEM log/exp/sin, tiles/outside/nonsquare; all supported callable/operator names and precedence in 361 array/mask vectors |
| C08 | Stretch, percentile, uint8 scaling/rounding, gamma, forward/inverse/zero sigmoidal, LCH saturation, channel selection, transform chains | P01/P02 range checks, `single-transform-*`, `rgb-transform-*`, `rgb-partial-*`; hidden masked-channel values exact; no blanket image tolerance |
| C09 | All 171 built-ins, explicit numeric/hex/RGB/RGBA classes, custom NPY files | Every built-in image/legend; `categorical-*`, `category-zero-*`, explicit float/empty/invalid cases; Fortran-order, name override, live replacement, invalid shape/dtype in edges; generated assets reused verbatim |
| C10 | Four read and four warp methods; nodata/internal/alpha masks, valid zero, empty/outside/sparse, dimensions and seams | 16 method pairs on Sentinel-2, DEM and WorldCover; alpha/valid-zero real-raster derivatives, fine-resolution nonsquare fallback; P01 seams; zoom 31/35/64/1024 boundary regression |
| C11 | GET `/swagger.json`, `/apidoc`, static favicon/ReDoc; HEAD, OPTIONS, disallowed methods, CORS, URL decoding, validation, version | `route-*`, `method-*`, `cors-*`, `preflight`, `url-*`, invalid options, old client handshake; configured literal/regex/wildcard/empty origins in edges |

C09's `generate_maps` helper is satisfied by retaining its frozen generated
assets, not by implementing a new Rust palette-generation command. C10 matches
the frozen maximum zoom (1023) and degenerate high-zoom behavior. No finite
matrix proves every combination of numeric input and geography; broad release
workloads and platform/backend checks remain P08.

## Visual evidence and numerical decision

[Side-by-side views](http/side-by-side.png) were inspected at ordinary tile size:
Python on the left, Rust on the right, covering palette, categorical class,
singleband transform chain, RGB saturation, partial RGB masks, and NDVI.
[DEM and masked transform-chain views](http/float32-and-chain.png) were also
inspected at native 256-pixel scale. All these decoded images are exact and show
no visible difference. [Retained original image pairs](http/retained-images.json)
identify the individual reference/Rust PNGs. Full image sets remain under ignored
`process/validation/artifacts/p04-*` and can be regenerated using the commands below.

RW-019 accepts only the explained NumPy SIMD versus Rust/libm float32
transcendental rounding: `exp(1)` is 2.7182819843292236 versus
2.7182817459106445. Typed oracle tests allow at most two float32 machine epsilons
relative to magnitude for those operations only. Masks, integer wrapping,
arithmetic, other dtype tests, categories and final image comparisons are not
relaxed. The real float32 DEM log/exp/sin comparisons are exact after rendering.

## Failures repaired and provenance of special fixtures

- RW-016: float-only computation materially changed unsigned NDVI (116,166
  decoded channels). Preserve GDAL dtype and NumPy promotion/overflow semantics;
  the retained NDVI fixture is now exact.
- RW-017: filling hidden masked values changed visible channels after saturation
  (up to 95 levels; chained transforms up to 77). Preserve hidden NumPy values
  and independent channel masks; both partial-mask regressions now pass exactly.
- RW-018: preserve legacy bare-string ordering, metadata-column 500s, broken
  `mod` arity, omitted POST spec entry and inconsistent reversed-range behavior.
- RW-020: packaged adapter loops crashed with exit 139. GDB observed an unmapped
  callback from `__nptl_deallocate_tsd` on an exiting GDAL worker, with another
  worker in `dlopen`. Worker-local library unloading preceded TLS destruction.
  A process-lifetime library and once-only registration fix the cause; repeated
  requests and 12 calls over four concurrent callers now pass in the package.
  The smallest maintained reproduction is `p04_adapters.py`; ignored failed
  runs remain in `p04-adapters`, `p04-adapters-debug`, and `p04-adapters-gdb`.
- Restore schema numeric-string/float coercion, raw path sorting, custom-palette
  reloads, sparse/empty tile behavior and high-zoom boundaries. Retained cases
  verify repairs rather than silently changing the oracle.

Image baselines use checksum-pinned real Sentinel-2, Copernicus DEM and ESA
WorldCover pixels. `p02_validate.create_partial_mask_raster` removes only a
red-channel mask window for channel-local propagation. `p04_edges.py` adds an
alpha band from the existing real mask, or a 100×100 valid-zero patch with no
nodata, to isolate those semantics while retaining the real scene. Its tiny
custom palette tests layout/reload/shape validation. Small expression arrays
isolate precedence, dtype, overflow and invalid arithmetic; they do not replace
real-raster image evidence. No claim is made of publisher-native alpha coverage.

## Reproduce and retain

See the P04 commands in [process/validation/README.md](../../README.md). Inputs come from
the existing offline corpus and frozen reference environment; service tests need
permission to bind loopback sockets. Use fresh output directories. No external
services are contacted. `run-manifest.json` records the commands actually run,
exit status, platform and checksums. The workspace has no initial commit, so
source hashes, the lockfile and toolchain pin identify the implementation.

Retained report copies omit only large static text bodies (length/hash retained),
which were fully compared at runtime and are also embedded/audited as assets.
Other JSON results, all requests, selected original image pairs, visual sheets,
logs and oracle vectors are retained. Ignored full reports/images, intermediate
failed runs and release packages are left intact for local investigation.

Not run or claimed in P04: actual AWS deployment/invocation, a new AL2023 target
build/container qualification of P04, other platforms/architectures, real S3 or
custom endpoint credentials, physical BigTIFF offsets, MySQL/PostgreSQL, full
legacy Python APIs/settings/CLI, or browser E2E. P03's earlier official-runtime
qualification remains historical evidence, not a new P04 deployment test.
