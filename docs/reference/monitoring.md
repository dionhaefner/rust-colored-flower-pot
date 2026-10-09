# Monitor request cost

Set `TC_EXPOSE_METRICS=true` to enable these application-owned diagnostic routes:

- `GET /__terracotta/metrics`: JSON counters, budgets, and `cost.stages`.
- `GET /__terracotta/metrics/prometheus`: Prometheus text format, version 0.0.4.

Both routes return 404 when disabled. Restrict enabled routes at your proxy or
deployment boundary. Enabling them does not add authentication. Native HTTP,
persistent `NativeApplication`, WSGI, and Lambda use the same application counters.
Separate applications and processes have separate counters and budgets. Counters
reset on application creation. Labels are a fixed stage vocabulary. Source paths,
dataset keys, query strings, signed URLs, credentials, and incoming trace IDs
never become metric labels.

## Stage timing

`rcfp_stage_duration_seconds` is a cumulative histogram with a `stage` label,
`_bucket`, `_count`, and `_sum`. Finite bucket limits in seconds are
0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, and 30, followed by
`+Inf`. JSON provides the same finite buckets, count, and `sum_seconds` under
`cost.stages`. Timings include failed operations. Counters are live snapshots.
Check accounting identities after work drains.

| Stage                  | Observable scope                                                                                     |
| ---------------------- | ---------------------------------------------------------------------------------------------------- |
| `selection`            | Render catalog lock/refresh, dataset descriptors, and selection. Metadata computation can occur here |
| `http_queue_wait`      | Native socket acceptance to an HTTP worker taking the socket. Excludes request parsing               |
| `queue_wait`           | Raster submission to worker dequeue, including jobs subsequently discarded                           |
| `source_access`        | Source generation checks and GDAL handle lookup/open                                                 |
| `remote_source_access` | The remote subset of `source_access`                                                                 |
| `raster_read`          | Requested-grid setup, decode, reprojection, masks, and samples after source lookup                   |
| `remote_raster_read`   | The remote subset of `raster_read`                                                                   |
| `coloring`             | Expression evaluation, normalization, color transforms, labels, and pixel packing                    |
| `encoding`             | PNG or WebP encoding                                                                                 |
| `render_wait`          | A follower waiting for an identical active render                                                    |
| `request`              | Shared HTTP contract handling until the response is ready, including rejected requests               |
| `delivery`             | Native HTTP response writes to the socket, including failed writes                                   |
| `prefetch`             | An attempted idle tile or decoded-block prefetch                                                     |

These are wall times, not CPU times. A request can use parallel source jobs.
`request` includes its stages, remote stages are subsets, and prefetch includes
its source/read work. Do not add histogram sums to obtain request duration.
Aggregate timings include background work. Python/WSGI/Lambda hosts own network
delivery, so their `delivery` and `http_queue_wait` observations remain zero.
Socket write completion does not establish browser receipt or decode time.

GDAL can fetch network ranges during both source opening and raster reads.
`remote_raster_read` combines I/O with decode/reprojection. It is not a measured
network-only duration. Exact GDAL HTTP/S3 request counts, transferred bytes,
block-cache hits, and transport retries are not available through these metrics.
Correlate remote stage time with origin/proxy/provider traffic measurements.
`remote_jobs` counts admitted remote jobs, including jobs later discarded, rather
than network requests or distinct assets.

## Counters and contention

Prometheus exposes raster counters as `rcfp_raster_<name>_total` and raster
gauges as `rcfp_raster_<name>`. Their names match the existing JSON fields:

- `submitted`, `completed`, `failed`, `cancelled_queued`, `overloaded`, and
  `timed_out`; `queued`, `in_flight`, `workers`, and `queue_capacity`.
- `cache_hits`, `cache_misses`, `cache_bytes`, `cache_capacity_bytes`, and
  `cache_entries` for the application array cache.
- `prefetch_completed`, `prefetch_failed`, and `prefetch_in_flight` for attempted
  background reads, accounted separately from admitted foreground jobs.
- `remote_jobs` for admitted remote work.

`rcfp_render_hits_total`, `rcfp_render_misses_total`,
`rcfp_render_coalesced_total`, and `rcfp_render_in_flight` describe finished-image
reuse and active render sharing. Render bytes, capacity, and entries are gauges.
`rcfp_render_admitted` counts requests holding render admission, including
coalesced followers. JSON places cache counters under `render_cache`.

`rcfp_source_handle_hits_total` and `rcfp_source_handle_misses_total` count
worker-local source-handle lookups and open attempts, including background work.
A handle hit does not imply a GDAL block or downloaded-range hit.
`rcfp_raster_backend_reads_total` counts pixel reader invocations with a fixed
`backend` label of `tiff` or `gdal`. JSON exposes these counts under
`cost.raster_backend_reads`. A failed TIFF attempt followed by GDAL fallback
increments both counters. Cache hits do not invoke a pixel reader. The shared
`raster_read` stage measures both backends and cannot identify which one ran.
`rcfp_http_overloaded_total` and `rcfp_render_overloaded_total` distinguish native
socket queue rejection from shared render admission rejection.
`rcfp_delivery_failed_total` counts failed native socket writes.
`rcfp_delivery_body_bytes_total` counts response body bytes only after successful
writes, excluding headers and partially written failed responses. These counters
are under `cost` in JSON.

High queue wait with workers busy suggests saturation. High remote source/read
time suggests investigating the source service and GDAL I/O options. High coloring
or encoding time identifies downstream rendering cost. Cache misses and source
handle misses distinguish the application caches. They do not measure lower-level
GDAL reuse. For example, a Prometheus p95 per stage is:

```text
histogram_quantile(0.95, sum by (stage, le) (rate(rcfp_stage_duration_seconds_bucket[5m])))
```

Foreground jobs use a bounded FIFO queue. Idle prefetch checks admitted foreground
work before taking another background read. An already executing prefetch/GDAL
call finishes normally. It cannot be preempted safely. Keep prefetch disabled
unless your measured workload benefits. Identical active singleband/RGB renders
share work even when finished-image retention is zero. Queued jobs whose waiters
have expired are discarded before GDAL access. Requests do not carry a viewport
identity, so a new view does not invalidate other URLs. A client disconnect is
observed at native delivery. It does not immediately revoke a render or cancel
GDAL. The configured wait deadline still bounds the response wait.

After draining, `submitted = completed + failed + cancelled_queued` and both
`queued` and `in_flight` are zero. `timed_out` counts expired waits and overlaps
those outcomes. Prefetch is separate. Application destruction joins admitted
native work. Python capsule destruction releases the GIL while joining, allowing
other Python threads to continue. Native server-loop exit joins its HTTP workers
before releasing the application. The native CLI closes HTTP admission and joins
admitted work on Unix SIGINT, SIGTERM and SIGHUP. On Windows, Ctrl+C and Ctrl+Break
request the same drain through the [signal handler](https://docs.rs/ctrlc/3.5.2/ctrlc/).
Embedded applications leave process signal handling to their host. Already
accepted HTTP requests keep their parsing and response deadlines, and expired
queued raster jobs are discarded. Running GDAL calls finish normally, so configure
remote-I/O timeouts and allow enough supervisor shutdown time. Forceful termination
such as SIGKILL or Windows process termination uses operating-system cleanup.

## Logs and traces

Request timing logs (`serve --profile`, debug, or info logging) include
`stages_seconds`. Methods and routes use fixed vocabularies. Paths and query
strings are omitted. With `TC_XRAY_PROFILE=true`, sampled incoming X-Ray requests
include the same timings under `metadata.rcfp.stages_seconds`. Raster jobs carry
the initiating request's timing context across worker threads. A coalesced
follower records its wait rather than claiming the leader's stages.

Deadline traces contain observations available when the response becomes ready.
An executing job can finish afterward. Its completed timings remain in aggregate
metrics but cannot retroactively modify the emitted trace. Delivery is recorded
after response creation and is therefore absent from the request trace. X-Ray
uses the configured daemon. There is no OTLP exporter or automatic collector.
