# Tune resource limits

Reuse a persistent `NativeApplication` or WSGI application. Each application
owns its workers, queue, and caches. Creating one per request discards the warm
state; adding WSGI processes multiplies the native pools and budgets.

```sh
export TC_RASTER_WORKERS=3
export TC_RASTER_QUEUE_CAPACITY=32
export TC_RASTER_TIMEOUT_MS=30000
export TC_RASTER_CACHE_BYTES=67108864
rcfp serve -d catalog.sqlite
```

Start with realistic concurrency and measure first-view completion, navigation,
p95 latency, queue saturation, cache misses, and total process memory. Increase
queue capacity only if the extra waiting fits your latency target. A larger
queue does not make GDAL reads faster. `USE_MULTIPROCESSING=false` changes the
worker default to one unless `TC_RASTER_WORKERS` overrides it.

Native cache budgets count uncompressed arrays. A Terracotta compressed-cache
budget with the same number need not retain the same images. Account separately
for GDAL's block cache, optional decoded-source cache, Python/host overhead, and
multiple processes. See [configuration](../reference/configuration.md) for
ranges and defaults.

Metrics at `/__terracotta/metrics` are hidden unless `TC_EXPOSE_METRICS=true`.
The same switch enables `/__terracotta/metrics/prometheus`. Use the fixed-stage
histograms to distinguish queue waiting, source opening, raster reads, coloring,
encoding and native delivery; [monitoring](../reference/monitoring.md) explains
counter scope, trace timing, and GDAL's remote-I/O visibility limits.
Expose them only through the intended monitoring route. Queue saturation returns
503; a wait deadline returns 504. Jobs still in the queue are discarded when their response waiter times out.
Already running GDAL work remains accounted and drains. Stateless tile requests
do not convey a viewport lifecycle; changing views does not cancel other URLs. Application
destruction joins workers after outstanding native work finishes; configure
remote I/O and graceful shutdown timeouts accordingly.
The native CLI drains on Unix SIGINT, SIGTERM and SIGHUP, or Windows Ctrl+C and
Ctrl+Break. It closes HTTP admission before waiting for accepted requests and
native reads. Python/WSGI/Lambda hosts control their own process shutdown.

Enable `TC_RUST_MERCATOR_FAST=1` or `TC_PREFETCH_TILE_RING=1` only after checking
representative images and budgets. `TC_RUST_UTM_FAST=1` is a native-mode opt-in
with sampling differences and is disabled by the compatibility profile. Consult
[performance](../explanation/performance.md) before interpreting speed claims.

## Tune the native map path

Native tiles use a direct output grid by default, with GDAL/PROJ handling source
CRSs and overview selection. This covers northern/southern UTM, geographic,
Web Mercator and rotated sources with scalar values and GDAL masks. Previews
retain the general reader. `TC_NATIVE_DIRECT_READ=false` restores the previous
tile reader; the Terracotta profile disables the direct reader automatically.
No application-specific CRS list selects the path. Eligible affine sources also
reuse worker-owned projection/overview plans; `TC_NATIVE_WARP_PLAN=false` restores
per-tile warp setup. Plans follow source invalidation and TTL; each of the
12 local and 12 remote source entries per worker can retain four additional
overview handles apiece.
GDAL's block budget remains shared across these datasets.

Native mode now defaults to 60-second remote source leases; the Terracotta
profile defaults to zero. Publish immutable/versioned URLs. Set
`TC_REMOTE_SOURCE_CACHE_TTL_SECONDS=0` to reopen on every read; reopening still
does not clear GDAL's downloaded ranges or guarantee immediate credential refresh.

For static remote assets and repeated navigation, try this allocation:

```sh
export TC_REMOTE_SOURCE_CACHE_TTL_SECONDS=60
export TC_RASTER_CACHE_BYTES=50331648
export TC_RENDER_CACHE_BYTES=16777216
export TC_REMOTE_RENDER_CACHE_TTL_SECONDS=60
```

It assigns 48 MiB to arrays and 16 MiB to finished images. Configure GDAL's own
budget separately. This is a starting experiment, not a universal memory limit.
Inspect image-cache hits, coalesced requests, bytes, queue metrics, CPU and RSS.
Identical active renders share work even with zero finished-image budget; there
is no completed-image retention at zero.

`TC_COMPACT_RASTER_CACHE=true` can fit more integer/Float32 arrays in the same
budget. It preserves sample bits and packs masks, retaining recent expanded f64
arrays only when that same budget permits. Loaded RGB revisits benefited in the
measured workload, but some fresh pans slowed, so the option defaults off.
Normalization uses exact AVX2 on supported x86_64 CPUs and scalar code elsewhere.
At nondefault PNG compression levels, bundled libdeflate can reduce encoder CPU;
level 1 retains the faster existing encoder. Nondefault PNG levels also need a
full filtered-image buffer per active encoder in the transient memory budget.
Neither choice requires repacking
rasters or changes decoded PNG pixels.
Local raster/mask/overview/auxiliary file stamps invalidate local reuse; publish
revisioned paths for VRT dependencies or remote assets. Confirm freshness and
credential refresh against your provider before retaining remote handles.

Keep prefetch off unless representative tracks improve: it can increase CPU
and hurt foreground work. RGB reads share the bounded worker queue rather than
creating coordination threads per channel. Nested GDAL threading and additional
WSGI processes can multiply work and memory without improving complete viewports.
SQLite descriptor caching is optional and usually a smaller opportunity than
sampling or source access; prepare metadata before serving.

For COG first views with noticeable source round-trip latency, experiment with
`TC_GDAL_OPTIONS='{"GDAL_INGESTED_BYTES_AT_OPEN":65536}'`. The paired range-server
trial improved median first-view completion about 1.23× and did little for warm
pans. A separate 128 KiB chunk trial improved first views about 1.25× but often
fetched more bytes; configure `CPL_VSIL_CURL_CHUNK_SIZE` through the same JSON map
only after measuring your files. The existing downloaded-range cache is separate
from GDAL's decoded block budget. See
[GDAL range access](https://gdal.org/en/stable/user/virtual_file_systems.html).
