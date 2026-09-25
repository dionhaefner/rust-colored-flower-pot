# Post-qualification bottleneck diagnostic — RW-043

The P08 benchmark matched CPU affinity and application cache budgets, but did
not match aggregate GDAL block-cache budgets: `GDAL_CACHEMAX=32` meant 32 MiB
shared across Rust threads and 32 MiB in each of three Gunicorn processes
(96 MiB aggregate). Original timings remain valid for those configurations;
the claim of equal cache/resource budgets is too broad.

GDAL documents its process-global block cache at
https://gdal.org/en/stable/user/configoptions.html#performance-and-caching .
The native/read and frozen Rasterio paths both execute GDAL. This is not an
interpreted-Python versus compiled-Rust pixel-processing comparison.

The retained diagnostic runs the final native binary, three-core affinity,
application cache disabled, six warmups then 18 requests at concurrency three.
All timed images compare exactly across configurations. These are short single
trials, not a replacement for P08's repeated full matrix.

| Native configuration | RGB preview p50 ms | Geographic DEM p50 ms |
| --- | --- | --- |
| One process, three workers, 32 MiB total GDAL cache | 1434 | 113 |
| One process, three workers, 96 MiB total GDAL cache | 768 | 60 |
| Three processes, one worker each, 10 MiB each (30 MiB total) | 1643 | 117 |
| Three processes, one worker each, 32 MiB each (96 MiB total) | 763 | 61 |

The shared-process preview consumed 25.83 CPU-seconds for the timed batch at
32 MiB versus 13.58 at 96 MiB. Both occupied approximately three cores. Increasing
cache capacity nearly halves actual CPU work, and splitting processes at the
same aggregate budget brings little improvement. This points strongly to
GDAL block-cache pressure and repeated raster work rather than dominant lock
waiting. Eviction/recomputation call stacks were not directly sampled: `perf`
was unavailable under the host's `perf_event_paranoid=4`; the retained log records
that restriction. No kernel setting was changed.

For context, original Python three-client p50 values were 736 ms RGB preview
and 74 ms geographic DEM. The corrected-budget Rust diagnostic is therefore
near Python for RGB and faster for DEM; a repeated matched rerun is still needed
before replacing the official performance summary.

A separate remaining compute-path candidate is visible in
`src/expressions.rs::evaluate`: each pixel executes a bytecode loop with variable
BTreeMap/string lookup, stack manipulation and dynamic dtype/mask handling.
The reference evaluates whole NumPy arrays. The original cache-on compute result
(6.46 ms Rust versus 4.67 ms Python at three clients) is consistent with this
extra overhead, but this diagnostic did not time that evaluator separately.
Uncached compute inputs also load sequentially in `src/http.rs`.

Next: rerun the full matrix with an explicit aggregate GDAL cache budget and
record per-stage raster/warp/expression timings. Optimize expression dispatch
only with the existing dtype/mask compatibility tests intact. No production
implementation, cache default, or sealed P08 archive was modified by this
investigation. The archives predate this addendum.
