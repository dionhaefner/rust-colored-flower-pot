# Native map-serving performance

The target workload is one multicore VM, a SQLite catalog with precomputed
metadata, local or remote COGs, and one or several users panning/zooming singleband
and RGB layers. Improvements are measured against the pre-change RCFP executable,
not against Terracotta. Native rendering has its own sampling contract;
[compatibility notes](compatibility-notes.md) govern the named Terracotta profile.

## Disposition of the ranked knobs

| Rank | Direction | Implementation and disposition |
| ---: | --- | --- |
| 1 | Prepared map grids/overviews | Implemented native `--web-mercator-grid`, `--block-size 256/512/1024`, `--all-bands`, map-compatible COG alignment, predictor and NaN padding for nodata-free float sources. Keep optional: preprocessing changes sampling and costs storage/build time. Small remote blocks regressed substantially; 1024 blocks restored broad gains. Static full pyramids are discarded for this workload: arbitrary bands/styles still need dynamic serving and an unbounded pyramid adds preparation/storage without measured demand. |
| 2 | General native sampler | Implemented and enabled by default in native mode. Contained north-up EPSG:3857 uses a floating RasterIO window; other source geometries use one requested-grid warped VRT through GDAL/PROJ. Supports source bands, scalar dtypes, overviews, nodata, positive explicit alpha and GDAL masks. Previews/non-map reads retain the general reader. No UTM zone allowlist. `TC_NATIVE_DIRECT_READ=false` rolls back; the Terracotta profile disables it. Retain GDAL transformations/decoding and its default overview/approximation policy. Rust now constructs eligible affine tile VRTs around reusable transformers rather than repeating the high-level utility setup. Unsupported cases retain the utility fallback. |
| 3 | Source/block reuse and S3 ranges | Implemented bounded worker-owned remote handle reuse through `TC_REMOTE_SOURCE_CACHE_TTL_SECONDS` (native default 60 s; named profile default 0; at most 12 source entries per worker). Local handle/array/image identities include main raster, mask, overview and auxiliary file generations. Native local opening probes known auxiliary files while remote sources retain directory suppression. GDAL retains its existing decoded/range caches; additional source singleflight and a second decoded cache are discarded without evidence of benefit over those caches. Existing `TC_GDAL_OPTIONS` supports measured range/header choices: 128 KiB chunks and 64 KiB opening ingestion improve first views but stay explicit because bytes, pans and format layouts differ. The broad direct path does not depend on the old UInt16-only source-block cache. |
| 4 | Scheduling/fairness | Implemented bounded RGB batch submission without three temporary coordination threads per request, shared same-asset multiband jobs, and abandonment of queued work after its waiter leaves. Active GDAL work remains accounted. Existing worker/queue/admission limits remain. Batch timings were neutral; retain the bounded ownership improvement. Strict source affinity is discarded because the common single-source workload must use several cores. More pools/nested GDAL threads add oversubscription. Automatic prefetch is discarded after the paired trial; retain the existing explicit opt-in. Viewport-wide cancellation/directional prefetch lack a viewport lifecycle in stateless tile requests and are not worth adding a new protocol here. |
| 5 | Buffers/fusion/SIMD | Implemented fused normalization/packing for transform-free singleband and RGB; default on, with `TC_FUSED_RENDER=false` rollback. Exact decoded pixels, including rounding ties, masks and nonfinite behavior, are retained. Color transforms use the existing ordered pipeline. Retain public f64 semantics. Runtime AVX2 now accelerates exact normalization with scalar fallback; RGB revisit gains are modest. Lossless private typed caching with packed masks and an expanded hot tier stays opt-in after fresh-pan regressions. |
| 6 | Finished images/coalescing | Implemented bounded LRU and identical-request coalescing with error/panic cleanup, timeouts, source generations, metadata/style/grid/band/format keys, and metrics. `TC_RENDER_CACHE_BYTES` defaults 0; remote retention additionally requires `TC_REMOTE_RENDER_CACHE_TTL_SECONDS` > 0. Active requests now coalesce even with zero budget; zero disables completed-image retention. Positive budget plus zero remote TTL also coalesces remote work. Budget allocated payload capacity plus estimated keys; cap 4096 retained entries. Keep optional and divide existing memory between arrays/images rather than silently increasing it. No external cache/CDN is required. |
| 7 | Multiband RGB/shared plans | Implemented one-based tile `band` and `r_band/g_band/b_band` selectors, one source lease/warp for matching same-file requests, and all-band grid preparation. Existing catalog rows can point to the same TIFF, so there is no catalog format fork. Nondefault bands require explicit numeric stretches; previews remain band 1. Bundling remains optional: matched-block RGB remote gains are modest and singleband regressions are large. Separate-file RGB reads still run concurrently through the bounded batch. Worker-local affine projection/overview plans now reuse setup without cross-file sharing. Requested bands are pruned only without nodata and with shared coverage; other sources keep all bands. Cross-file sharing remains discarded because independent geometry/overview/mask lifetimes add coordination without demonstrated benefit. |
| 8 | Encoding/formats | Implemented direct PNG compression at nondefault levels, removing encode/decode/recompress; fast level 1 stays default. Bundled libdeflate improves nondefault levels with lossless fallback; it regressed level 1 and is not used there. Implemented native RGB `format=webp&quality=85` with direct libwebp encoding and unchanged binary coverage, including transparent edge tiles and Lambda binary responses. PNG stays default: WebP trades more CPU/slower loopback completion for fewer client bytes. Discard JPEG because it loses required alpha and discard making lossy formats automatic for scientific/category imagery. Lossless WebP's limited size advantage does not justify another default path. |
| 9 | Descriptors/SQLite | Implemented prepared-statement reuse and optional bounded descriptor cache (`TC_CATALOG_CACHE_BYTES`, default 0; cap 256), checked through SQLite data_version and invalidated on own writes. Transactions bypass retention; coherent read snapshots fall back uncached during repeated external writes. Equal-budget viewport results are neutral. Discard a connection-pool/backend rewrite and listing optimizations for pan/zoom: neither eliminates raster/encoding work, and listing is outside each tile request. Prepare metadata before serving rather than introducing asynchronous readiness publication. |

“Discarded” means no implementation investment/default activation for this
workload, with the reason above. It does not claim that every alternative was
benchmarked or that it cannot benefit a different product requirement. Encoding
and descriptor mechanisms with modest timings remain small, useful opt-ins;
there is no remaining ranked implementation task hidden behind an unspecified
future phase.

## Additional measured optimizations

The next round starts from the executable used in the cross-service comparison,
SHA-256 `4624c1e46b84613b6d496ed9a928685bab62d3a696631f64e89898dbec8f7ed3`.
Evidence lives in ignored `build/aggressive-performance-20261006/`; experiment
names identify frozen artifacts, not supported product modes. The native reader,
fusion and prepared statements already existed in that baseline. No derivatives,
encoded-image cache, prefetch, custom range chunks or fixture-specific selectors
are needed for the new default gains.

| Idea | Evidence and disposition |
| --- | --- |
| Reusable affine warp plans | `plan-pair`: three paired one-user trials across six modes and both storage policies, before changing source lease defaults. Local pans improve roughly 1.07–2.43×. Retain by default for eligible native affine sources. Source-owned base/overview transformers replace repeated GDALWarp option parsing, suggested-grid and projection setup. Preserve GDAL AUTO overview selection and 0.125-pixel approximation. Unsupported sources, uncached remote handles and working-dtype promotion use the utility fallback. |
| Remote source leases | Default to 60 seconds in native mode, zero in the named profile; explicit zero works. The final combined trial measures the actual parsed defaults. Immutable/revisioned remote paths are required for snapshot semantics; handle TTL is not an ETag or credential-refresh guarantee. The earlier directory named `leases-pair` still had TTL zero because the settings template overrode the experimental fallback, so it is **not** lease evidence. |
| Active-render sharing at zero cache budget | `coalescing-pair`: both sides explicitly use 60-second source leases; three trials include one/three overlapping/independent users. Median overlapping pan gains about 1.28× local and 1.16× range, with exact images. Retain at every retention budget. Work is shared only while active; admission bounds flights and failure/panic cleanup wakes followers. |
| Lossless private typed cache | `compact-pair` and `hot-pair`: same binaries, explicit on/off controls, identical logical budgets. Loaded independent RGB revisits improve about 1.7–2.1×, but fresh pans regress in some cells, down to 0.78× in the hot-tier experiment. Keep **off by default**. Integer/Float32 samples pack only if casting back preserves every f64 bit, including hidden masked values; masks pack to bits. Compact and recent expanded copies share the existing byte budget. |
| Exact AVX2 normalization | `simd-pair`: same binary, on/off control, three trials across 36 cells; exact images. Group navigation medians are near 1.0×; RGB revisits improve about 1.04–1.21×. Retain the small isolated implementation with runtime AVX2 detection, unchanged arithmetic/rounding and scalar fallback; no ISA baseline or reduced-precision change. |
| Dense categorical lookup | Generic bounded integer-label tables replace per-sample palette scans; arbitrary/large/fractional palettes fall back. Preserve duplicate-first-match, masks, unknown classes and category colors. The final combined workload covers classes, without a WorldCover-specific label table. |
| Requested-band pruning | Affine plans warp only requested bands when all source data bands lack nodata and coverage is shared. Per-band nodata/masks retain all-band coverage. The real masked four-band derivative exercises this independently; the existing nodata-bearing Landsat timing input cannot substantiate an isolated pruning gain. |
| Nondefault lossless PNG compression | The isolated real-navigation-image encoder probe performs 1,800 RGB encodes per configuration. The final isolated probe (`image-benchmark-isolated.log`) takes 10.328 s at level 6 with the Rust compressor versus 3.144 s with bundled libdeflate: 3.28× less encoder time and 2.5% fewer bytes, exact decoded pixels. At level 1 libdeflate took 1.569 s versus 0.515 s: discard it for the default level. Missing native codec libraries retain the Rust fallback. Compression buffers remain transient and nondefault levels now build the complete filtered input. These are encoder CPU measurements, not viewport gains. |
| Anonymous utility VRT destination | `anonymous-pair`: no consistent broad navigation gain; discarded. |
| Integer alpha/mask Byte reads | `masks-pair`: three paired trials over 12 one-user cells; exact images, but median pan ratio 0.97× and mixed regressions despite smaller temporary buffers. Discarded; f64 alpha reads remain. |
| Standalone Rust TIFF/warp backend | Not implemented. Stage profiling places repeated utility setup at about 0.75–2.48 ms per tile and total reads at 2.7–14 ms. Rust plan ownership removes that orchestration first. Decoding, projection kernels and required source I/O remain; a second backend would need format/mask/overview/CRS qualification without eliminating cold-source round trips. The current evidence does not justify that investment or a universal 2–5× promise. |

The retained binary passes 249 independent checks per resampling policy (996
total), both profiles, local freshness and sustained resource gates. Exact
artifact identities and separate platform exclusions are in the
[validation handoff](human-validation.md#additional-native-optimization-qualification).

The final guarded executable, SHA-256
`e62c7b6bc8a54c91e57c3674ae9da2099c064a77e1ea080515432cdc3cbf69a3`,
is measured in `guarded-pair`: three alternating trials over 36 cells,
four pinned cores/workers, 64 MiB GDAL + 64 MiB arrays, unchanged curl budget,
zero finished-image retention and default 60-second native source leases.
All paired decoded images match the original baseline. Ratios below are
medians of per-cell baseline/candidate ratios; gains are not multiplied.

| Storage | First | Pan | Zoom | Revisit | CPU ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| Local | 1.21× | 1.49× | 1.23× | 1.02× | 1.45× |
| Remote ranges | 1.18× | 2.76× | 1.77× | 1.04× | 2.00× |

First views retain PROJ initialization and compulsory source reads; revisits often
already hit the array cache and client/HTTP floor. These mechanisms therefore
cannot deliver another 2–5× on every action. Keep raw cell distributions and
failures; do not multiply isolated gains or relabel controlled range traffic as
actual S3. Hardware `perf` counters were unavailable under this host's kernel
policy; stage timers supplied the profiling evidence instead.

## Representative navigation benchmark

Maintained acquisition, preparation, navigation and paired comparison live in
`tests/navigation_*.py`; source URLs, attribution, sizes and SHA-256 values are in
`tests/fixtures/navigation.json`. Inputs include full 10980² UInt16 Sentinel-2
bands (EPSG:32632), 3600² Float32 DEMs in both hemispheres (EPSG:4326), full
36000² UInt8 WorldCover classes, and real multiband Byte Landsat imagery in
EPSG:32618. The small Landsat source supplements the full-scene workload; it is
not the basis for the large-raster gain claims. No algorithm selects fixture
names, extents, CRS zones, or benchmark zooms.

A fresh SQLite/service per case uses four pinned cores/workers, four simultaneous
requests, a bounded queue, metadata computed before timing, and a fixed 3×3
viewport at three zooms. Tracks pan incrementally and revisit the starting view.
One and three users cover identical/overlapping and independently displaced
views. Timings are viewport response completion including Pillow decode, with
per-request receipt p50/p95/p99; they do not include browser paint. Every request
records status, dimensions, decoded hash and valid coverage. CPU/RSS/threads,
cache/queue metrics, source GET/range counts/bytes and exact artifact/settings
identities accompany reports. CPU/RSS are sampled from the service, not summed
Python worker RSS. Peak resource bounds have a separate sustained test.

Storage modes are local, a controlled HTTP byte-range server with 15 ms request
latency, and actual public S3 URLs. The range server is a remote-access surrogate,
not evidence of production S3 throughput. Local trials have a warm OS page cache
and fresh service/GDAL state, not physically cold storage. Sequential alternating
paired trials use three repetitions and matched total logical cache budgets;
reports preserve individual cells, tails and failures rather than just a
headline ratio. A derivative manifest retains original navigation bounds so
padding cannot silently change the requested route. RGB selector/format
comparisons canonicalize only their explicit mapping/format differences and
verify dimensions and coverage. Derivative/sample changes need independent
GDAL grid checks; benchmark images are never reference goldens.

## Measured decisions

Local Linux x86_64, GDAL 3.10.3. Each value below summarizes medians of three
paired per-cell trials. Group summaries do not promise that every cell improves.
Generated reports, frozen executables and logs are retained under ignored
`build/navigation-performance/`.

| Isolated experiment | Evidence and consequence |
| --- | --- |
| General reader (`paired-direct`) | Local imagery first/pan/zoom about 1.45/1.65/1.67×; range imagery 1.18/1.21/1.28×. Other raster families vary; some remote zoom cells regress. Retain the generic path, independently qualify its geometry/coverage. |
| Remote source leases (`paired-source`) | Range pans about 2.17× imagery, 2.91× Alps DEM, 2.95× southern DEM; class/Byte sources nearly neutral. Sentinel bytes about 41% lower; DEM about 51% lower. Pixels exact. Benefits depend on spatial reuse; remote retention was explicit in that experiment; it now defaults to 60 s in native mode. |
| Bounded batching (`paired-batch`) | RGB viewport ratios about 1.0×, pixels exact. Keep for bounded coordination/cancellation, not a latency claim. |
| Fusion (`paired-fusion`) | Exact pixels. RGB pans about 1.11×, revisits 1.23×, CPU ratio 1.07×. Singleband pans about 1.07×. Enable by default. |
| Encoded cache (`paired-cache`) | Equal total budget, exact pixels. RGB revisits about 1.51× and approximately 23% lower CPU. Fresh zooms mixed. The experiment's earlier remote cache used the array TTL; current reproduction must set remote image TTL explicitly. |
| Map grid, 256 blocks (`paired-prepared`) | Local improvements but major remote regressions from additional ranges; imagery remote pan ratio about 0.58×. Discard a universal 256-block prescription. These older preparation results are performance evidence, not final alpha qualification. |
| Map grid, 1024 blocks/predictor (`paired-large-block`) | Local imagery first/pan/zoom about 1.98/2.12/2.27×; range imagery 2.16/2.93/2.23×. Remote pans 1.36× Alps, 2.15× south, 1.89× Byte imagery; Alps remote zoom about 0.80×. Keep block size selectable and preparation optional. |
| Matching 1024-block bundled RGB (`paired-multiband-large`) | Range RGB first/pan/zoom about 1.32/1.13/1.29×; local first about 0.74× and pans 1.08×. Singleband local pans about 0.52× and range pans 0.46×. Bundling is an RGB-heavy tradeoff, not a universal upgrade. Overview construction changes samples; do not describe those images as exact. |
| Prefetch (`paired-prefetch`) | Local pans about 1.08× but revisits 0.91×, about 37% more CPU; range results about 1.0×. Keep off by default. |
| PNG level 6 (`paired-encoder`) | Exact decoded images; viewport ratios approximately 1.0×, CPU ratio 1.01×. Median render stage 5377 → 5020 µs; metadata 91 → 59 µs (prepared-statement reuse also differs). Remove redundant encoding work without advertising a large speedup. Level 1 is unchanged. |
| RGB WebP (`paired-webp`) | About 13.29× fewer response bytes, exact alpha/dimensions; local pan ratio 0.85× and revisit 0.54×, approximately 27% more CPU. Keep explicit for bandwidth-limited visual RGB. A separate Pillow feasibility probe is not native encoder timing. |
| 128 KiB range chunks (`paired-chunks`) | Exact images, fixed budgets. Median first viewport 1.25×, pans 1.02×, zooms 1.0×. Scene first-view gains 1.17–1.36×; source bytes increase roughly 5–19% for four groups and fall for Byte imagery. Keep an explicit GDAL option, not a universal chunk prescription. |
| 64 KiB header ingestion (`paired-headers`) | Exact images, fixed budgets. Median first viewport 1.23×, pans 1.0×, zooms 0.98×, total source bytes nearly unchanged. Keep explicit for COG first views; warm pans do not benefit. |
| Descriptor cache (`paired-catalog`) | Matched 64 MiB arrays versus 60 MiB arrays + 4 MiB descriptors; viewport/CPU ratios about 1.0×, exact pixels. Leave disabled by default. |

For 1024-block derivatives, single-source preparation took about 0.44–9.82 s:
Sentinel bands approximately 89–90 MiB → 45.5–45.9 MiB each, Alps DEM
41.2 → 38.5 MiB, southern DEM 32.2 → 24.7 MiB. Bundled RGB took 18.54 s and
increased source size 269.0 → 294.6 MiB. These are local build measurements with
one source-preparation worker, not server-request latency or universal costs.

## Combined existing-asset result

`paired-combined` compares the original RCFP baseline against native direct
sampling + fusion + remote source leases + 16 MiB encoded images. Both sides
have 128 MiB logical GDAL/application budgets: baseline 64 GDAL + 64 arrays;
candidate 64 GDAL + 48 arrays + 16 images. Decoded-source cache, descriptor cache
and prefetch are disabled. Stable remote fixtures have source/image TTL 60 s.
Neither prepared grids nor bundled RGB nor WebP are needed for these results.

| Workload | Storage | First viewport | Pan | Zoom | Revisit |
| --- | --- | ---: | ---: | ---: | ---: |
| Sentinel singleband | local | 1.73× | 1.31× | 1.63× | 1.03× |
| Sentinel RGB | local | 2.10× | 1.71× | 1.73× | 2.03× |
| Alps elevation | local | 1.80× | 1.04× | 1.26× | 1.08× |
| Classes | local | 1.99× | 1.12× | 1.15× | 1.04× |
| Southern elevation | local | 1.89× | 1.21× | 1.27× | 1.06× |
| Byte imagery | local | 2.65× | 1.36× | 1.49× | 1.07× |
| Sentinel singleband | range | 1.24× | 3.10× | 1.99× | 1.08× |
| Sentinel RGB | range | 1.37× | 1.99× | 1.73× | 1.95× |
| Alps elevation | range | 1.32× | 2.44× | 4.03× | 1.09× |
| Classes | range | 1.23× | 1.80× | 1.24× | 1.03× |
| Southern elevation | range | 1.31× | 3.05× | 1.48× | 1.05× |
| Byte imagery | range | 1.45× | 1.62× | 1.73× | 1.08× |

Ratios are baseline/candidate; >1 means faster. Each group summarizes one-user,
three-user overlap, and three-user independent cells. All 216 case runs complete
successfully; the full report contains tails and CPU rather than treating a group
median as a loaded-p95 guarantee. Combined CPU ratios range about 1.39–2.72×.
Gains overlap and must not be multiplied together.

## Qualification and exclusions

`native_rendering.py` independently invokes GDAL's public warp utility/Rasterio
reads and checks the requested affine, exact coverage, categorical RGBA identity,
and continuous channels within one byte. The earlier 192 image checks plus one Lambda check per policy covers northern and
southern UTM, geographic/3857, real rotated data, unaligned Mercator grids, polar/dateline sources,
Byte/UInt16/Float32, explicit fractional floating alpha, source edges, overview
zooms, square/rectangular outputs, same-asset RGB and WebP coverage. Each of
nearest/bilinear/cubic/average passed all 193 checks; categories always select nearest. Fractional
alpha is a narrowly generated edge mask over real values. This does not qualify
every GDAL driver or all preexisting metadata/preview issues.

Both profiles' standalone contracts pass. Cache freshness checks pass external
SQLite writes/deletion, main-raster mutation, external mask creation/replacement,
and dimension identity. Unit checks exercise eviction, remote TTL, singleflight,
leader failure/panic cleanup, batching and queued cancellation. A 180 s direct
sampler run with eight clients/three raster workers completed 152815 HTTP
requests with zero failures/overloads/timeouts, peak RSS about 75.7 MiB and file
descriptors 6 → peak 18 → 9. It exercises uncached rendering, not a large resident
image-cache working set.

The initial public S3 run failed because unpacked libcurl referenced its conda
build-time CA path. Bundled CA staging/runtime selection now preserves verified
TLS and explicit operator overrides. The verified broad S3 control completed
1701 requests across nine cases with three 504 RGB timeouts; this failed control
is excluded from speed ratios. The verified public S3 elevation experiment (`paired-s3-elevation`) completed
3402 requests in 18 case runs without failures. First-view ratios range
0.99–1.20×, pans 2.09–3.78×, zooms 15.07–23.06×, revisits about 1.04×. Large
zoom gains reflect this warmed, spatially overlapping source-access workload
and are not a general S3 claim. Loaded receipt p95 improves in two cells and
regresses in the independent three-user cell (1079.5 → 1109.8 ms). No S3 source
GET/byte counter is available from this client-side measurement. The optimized Sentinel singleband/RGB S3 reliability sweep
(`s3-imagery-candidate`) completed 1134 requests across six cases without
failures in one trial. It cannot supply a paired speed ratio against the failed
control. Range-server evidence is not relabeled as S3 evidence.

Earlier concurrent/incomplete timing experiments (`baseline-expanded`,
`direct-all`) are excluded. Physically cold disk, browser paint/WAN, private S3
credential rotation, mutable-object freshness, hosted OS/ISA/wheels and fresh
release artifacts are not measured here. Existing broader correctness/reference
qualification limits remain in [human validation](human-validation.md). Retain
failed/setup runs separately; prepared workflows are not passes.

Reproduction commands and interpretation belong in [tests](../tests/README.md),
operator knobs in [configuration](../docs/reference/configuration.md) and
[resource tuning](../docs/how-to/resources.md). A production deployment should
select preparation, remote retention and response format from representative
tracks rather than adopting every option simultaneously.

Installed Linux wheel checks passed 14 Python/WSGI/CLI contract cases, and a
nonempty native Python RGB check confirms default direct/fused behavior, source
selectors and exact WebP coverage. Documentation references were regenerated
from that installed wheel using the preserved Hatch VCS version; Sphinx and all
4159 local links passed. This host build is not a portable release wheel:
verification rejects its raw Linux tag and repair to manylinux_2_34 fails due to
symbols requiring manylinux_2_39. Keep those failed portability checks separate
from local API passes. Fresh pinned Terracotta differential replay was not run
in this environment; standalone named-profile gates are not that replay.

## Cross-service navigation evidence

The maintained `tests/service_benchmark.py` comparison completed three rotating
fresh-process trials against Terracotta 0.10.1 and TiTiler 2.4.0 / rio-tiler
9.4.9, with GDAL 3.10.3 throughout. All 216 case runs / 45,360 requests succeeded.
It measures the native executable SHA-256
`4624c1e46b84613b6d496ed9a928685bab62d3a696631f64e89898dbec8f7ed3`,
with four physical cores, 160 MiB aggregate block/array/curl budgets, no encoded
cache or remote dataset leases, the original full imagery/DEM/classes sources,
singleband/three-source RGB and one/three users from local/range storage.

First-view group ratios against Terracotta/TiTiler are about 1.5–4.2×; pan
ratios 1.9–9.5×. Results distinguish new tiles, zooms, revisits, stretches,
expressions, request tails, CPU, process-tree PSS/USS and exact origin bytes.
All local/range image hashes matched within each service. Independent direct
GDAL reads matched all 252 native saved-tile cases exactly; the other services'
grid/overview/rounding and category/edge differences are retained as differences,
not exact-fidelity passes. A direct-grid oracle is not an assertion that every
other documented sampling contract must produce identical pixels.

Headline figures and detailed methods are in the root README and
[public performance explanation](../docs/explanation/performance.md#terracotta-and-titiler-navigation-comparison).
Full results, snapshots and installed code fingerprints stay in ignored
`build/service-benchmark-default-concurrency/` and
`build/service-benchmark-quality-default-concurrency/`. TiTiler uses its default
request threadpool and all four worker PIDs must report health before timing.
The earlier one-render-per-worker comparison, interrupted palette-adapter run
and setup/smoke runs are excluded from headline timing evidence. This addresses the common map-serving
subset of issue #8. Its remaining scope is a representative real deployment with
the intended client network and S3 storage. Cold disk, detailed browser profiling,
time-series and expanded packed-multiband/mosaic comparisons remain applicability
limits. Deadline/disconnect behavior is a separate request-lifecycle correctness
gate, and this is not release qualification.

The follow-up cross-service run in `build/aggressive-performance-20261006/services`
measures SHA-256 `ecfdc05bbe70aa46354ee996d97c71dfb2a1a8d07845330b2294f023a29a1c8c`,
with all new default hot-path changes and 60-second source leases. All 216 cases /
45,360 requests succeed. First-view group gains against Terracotta/TiTiler are
1.7–6.0× and pans 5.2–18.8×. Loaded native p95 is 76.77 ms versus 340.76/351.03 ms;
median sampled peak PSS is 214/299/546 MiB, retaining the source-plan memory cost.
Native source GETs/fetched MiB are 1,856/1,164 versus 7,462/5,275 and 10,890/8,942.
All 252 native independent saved-image references match exactly, including 54
category tiles; the other services retain the documented numerical/coverage
classification. Local/range request-image comparisons pass separately.

The cross-service binary precedes only the conservative three-axis eligibility
fallback; all benchmark inputs have two-axis source CRSs. The final guarded
binary has its own full paired navigation and 996-check qualification above.
Do not conflate those artifact hashes or call the earlier resource/wheel run
qualification of the guarded artifact. Headline numbers are measured artifacts,
not inferred products of individual gains.
