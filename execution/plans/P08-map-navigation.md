# P08 follow-up — map navigation performance

Measure the user-facing serving path: how long a viewport waits for newly visible
singleband/RGB tiles, for one session and concurrent sessions. This is a local
server benchmark, not a claim about browser animation or WAN latency.

Use the pinned full Sentinel-2 RGB COGs (original overviews/encoding), frozen
Python in three Gunicorn workers and the final native binary with three raster
workers. Match three-core affinity, 96 MiB aggregate GDAL cache and 64 MiB
aggregate application-cache budget. Record physical RSS separately; caches have
different representations. Allow the same client workload without hidden retries.

Model a 1024×768 viewport (4×3 256-pixel tiles), six in-flight HTTP requests per
session, and deterministic horizontal/vertical pans at zoom 13. A session keeps
already loaded tiles, fetching only newly visible tiles. Use 1/4/8 sessions with
spatially offset tracks, retaining actual overlap/cache-hit evidence. Separate
fresh-service first-view completion from subsequent pan completion and individual
tile latency. Each pan follows completion plus a stated 100 ms pause. This does
not simulate continuous animation/cancelled requests.

Run three repetitions per engine/mode/session count, alternating engine order;
record p50/p95 viewport completion, requests, failures, tile latency, CPU/RSS,
native cache/queue metrics, input/output hashes and the exact commands. Decode
and compare every successful image against the frozen Python; record visible
coverage to expose empty-tile bias. Use full parents to avoid panning outside a
small crop. Every measured tile must have at least 95% opaque real-image
coverage; the initial bounding-box-only tracks crossed substantial nodata and
are excluded from performance claims. A superseded valid-track attempt that
briefly overlapped that pilot was stopped; only the isolated final run qualifies. Correct harness/implementation failures rather than hiding them.

Retain evidence and revise performance conclusions around navigation. No runtime
optimization, cloud check, dependency change or resealing of the earlier P08
candidate is implied by this measurement task.

Completed: 36 isolated runs, 4,992 requests and 412 distinct tile URLs; every
image exact, zero HTTP failures, all tiles 100% opaque. Evidence:
`validation/evidence/p08/navigation/README.md`. Initial nodata-heavy pilot and
superseded overlapping run are retained but excluded. No application code
changed; test services were shut down by their owners. Earlier sealed release
artifacts are unchanged. Next performance work should use this pan workload
and stage timings rather than expression/repeated-tile headlines.
