# Measured performance — Linux x86_64

For user-facing singleband/RGB browsing, use the newer [map navigation results](navigation/README.md).
They measure viewport completion for newly visible tiles and concurrent sessions
with corrected aggregate cache budgets. The table below is the older isolated,
repeated-request matrix and should not be read as scrolling performance.

> Post-qualification correction (RW-043): GDAL cache budgets were unequal:
> 32 MiB shared in Rust versus 32 MiB per Python worker (96 MiB aggregate).
> The original measurements below are retained, but do not prove equal aggregate
> cache performance. [A targeted diagnostic](bottleneck-followup/README.md)
> nearly halves the native preview cost with a matched 96 MiB budget. A repeated
> corrected matrix remains pending. Sealed P08 archives predate this addendum.

Sequential engine trials, three CPU affinity cores, three sync Gunicorn workers versus three native raster workers; native threads and BLAS capped. Primary cache-off comparison; cache-on uses total 64 MiB logical budget (Python compressed per worker, Rust uncompressed shared). OS page cache is warm, not dropped. Cold means fresh service process and first raster request, not cold disk. RSS is sum of service process-tree RSS including shared pages; 20ms samples. Loopback range server adds 2ms per GET. No external network performance claim.

Three trials per engine/access/cache configuration, 36 timed requests per workload/concurrency in each trial. Each response is checked for exact decoded RGBA against the oracle. Throughput includes client dispatch and image checking; latency samples end when the body is received. Each workload is warmed before timing. `geographic-dem` always uses the local DEM, including range-labeled runs. Fresh-process latency is measured on the first singleband tile only. No cold-disk, WAN or cloud startup claim.

Trial ranges describe observed variation, not confidence intervals. Sum-of-RSS double-counts shared pages in the multi-process Python server; it is not unique resident memory or PSS. The identical 1.5 GiB service-tree RSS guard is checked, not a kernel-enforced cgroup limit. Three CPU cores are affinity-constrained for each service; the load generator/range server run outside that affinity.

| Access | App cache | Workload | Clients | Python median latency ms (trial range) | Rust median latency ms (trial range) | Python / Rust latency |
| --- | --- | --- | --- | --- | --- | --- |
| local | off | compute-tile | 1 | 78.35 (77.91–78.41) | 99.48 (95.12–115.24) | 0.79× |
| local | off | compute-tile | 3 | 83.12 (82.78–83.30) | 82.40 (82.05–82.96) | 1.01× |
| local | off | geographic-dem | 1 | 71.04 (70.98–78.21) | 59.90 (59.66–60.47) | 1.19× |
| local | off | geographic-dem | 3 | 73.87 (69.01–76.89) | 111.10 (110.72–112.00) | 0.66× |
| local | off | high-zoom | 1 | 19.78 (19.47–22.71) | 11.98 (10.07–12.06) | 1.65× |
| local | off | high-zoom | 3 | 14.42 (12.81–14.84) | 7.29 (7.23–7.41) | 1.98× |
| local | off | preview | 1 | 728.20 (722.24–729.81) | 481.55 (479.26–526.07) | 1.51× |
| local | off | preview | 3 | 736.32 (733.05–737.13) | 1413.86 (1379.82–1417.31) | 0.52× |
| local | off | rgb-tile | 1 | 124.92 (124.39–125.70) | 46.32 (46.07–50.34) | 2.70× |
| local | off | rgb-tile | 3 | 133.68 (133.00–134.10) | 117.66 (116.14–119.55) | 1.14× |
| local | off | singleband-tile | 1 | 50.16 (49.44–50.50) | 41.51 (41.40–42.31) | 1.21× |
| local | off | singleband-tile | 3 | 49.50 (49.18–50.54) | 42.17 (41.81–43.21) | 1.17× |
| local | 64 MiB | compute-tile | 1 | 4.49 (4.34–5.65) | 6.48 (6.45–8.57) | 0.69× |
| local | 64 MiB | compute-tile | 3 | 4.67 (4.57–4.78) | 6.46 (5.66–6.62) | 0.72× |
| local | 64 MiB | geographic-dem | 1 | 4.64 (4.59–7.65) | 0.72 (0.72–0.73) | 6.43× |
| local | 64 MiB | geographic-dem | 3 | 4.83 (4.62–7.65) | 1.08 (1.08–1.21) | 4.47× |
| local | 64 MiB | high-zoom | 1 | 4.25 (3.61–4.78) | 0.71 (0.70–0.73) | 5.99× |
| local | 64 MiB | high-zoom | 3 | 4.31 (3.81–4.37) | 1.15 (0.89–1.18) | 3.74× |
| local | 64 MiB | preview | 1 | 11.10 (10.99–11.14) | 2.24 (2.22–2.97) | 4.95× |
| local | 64 MiB | preview | 3 | 11.21 (11.06–11.57) | 3.03 (2.89–3.15) | 3.70× |
| local | 64 MiB | rgb-tile | 1 | 11.74 (11.69–11.82) | 2.48 (2.47–3.04) | 4.73× |
| local | 64 MiB | rgb-tile | 3 | 12.16 (12.04–13.12) | 3.26 (3.24–3.48) | 3.73× |
| local | 64 MiB | singleband-tile | 1 | 4.51 (4.48–4.85) | 0.77 (0.77–0.78) | 5.83× |
| local | 64 MiB | singleband-tile | 3 | 4.74 (4.72–5.11) | 1.28 (1.16–1.31) | 3.70× |
| range | off | compute-tile | 1 | 77.17 (76.28–85.83) | 94.84 (82.20–95.68) | 0.81× |
| range | off | compute-tile | 3 | 81.30 (80.98–81.41) | 78.93 (78.50–80.59) | 1.03× |
| range | off | geographic-dem | 1 | 70.93 (70.82–71.02) | 57.77 (57.68–57.91) | 1.23× |
| range | off | geographic-dem | 3 | 69.40 (67.66–71.03) | 110.95 (110.95–111.32) | 0.63× |
| range | off | high-zoom | 1 | 22.90 (22.49–23.01) | 10.00 (9.11–10.23) | 2.29× |
| range | off | high-zoom | 3 | 13.14 (12.83–13.27) | 7.04 (6.96–7.13) | 1.87× |
| range | off | preview | 1 | 721.98 (720.91–724.19) | 478.14 (477.18–516.90) | 1.51× |
| range | off | preview | 3 | 732.06 (730.88–732.57) | 1407.67 (1406.48–1424.68) | 0.52× |
| range | off | rgb-tile | 1 | 120.23 (120.14–121.01) | 43.83 (42.21–44.33) | 2.74× |
| range | off | rgb-tile | 3 | 128.40 (127.15–128.67) | 111.47 (108.17–112.66) | 1.15× |
| range | off | singleband-tile | 1 | 47.76 (46.85–51.13) | 40.02 (39.54–41.21) | 1.19× |
| range | off | singleband-tile | 3 | 45.82 (45.71–52.14) | 40.36 (40.28–41.09) | 1.14× |
| range | 64 MiB | compute-tile | 1 | 4.34 (4.31–4.44) | 5.47 (5.40–6.39) | 0.79× |
| range | 64 MiB | compute-tile | 3 | 4.84 (4.59–5.08) | 5.72 (5.71–5.78) | 0.85× |
| range | 64 MiB | geographic-dem | 1 | 4.00 (3.99–4.00) | 0.70 (0.69–0.70) | 5.72× |
| range | 64 MiB | geographic-dem | 3 | 4.36 (4.25–4.88) | 1.19 (0.90–1.20) | 3.66× |
| range | 64 MiB | high-zoom | 1 | 3.62 (3.43–6.19) | 0.70 (0.70–0.73) | 5.16× |
| range | 64 MiB | high-zoom | 3 | 3.86 (3.61–4.08) | 1.24 (0.85–1.27) | 3.13× |
| range | 64 MiB | preview | 1 | 11.44 (11.42–11.47) | 1.60 (1.59–1.62) | 7.14× |
| range | 64 MiB | preview | 3 | 11.51 (11.45–11.52) | 2.35 (2.15–2.37) | 4.90× |
| range | 64 MiB | rgb-tile | 1 | 11.94 (11.93–11.99) | 1.83 (1.82–1.92) | 6.52× |
| range | 64 MiB | rgb-tile | 3 | 12.23 (12.22–12.37) | 2.45 (2.22–2.70) | 4.99× |
| range | 64 MiB | singleband-tile | 1 | 4.68 (4.56–5.37) | 0.76 (0.75–1.16) | 6.19× |
| range | 64 MiB | singleband-tile | 3 | 4.77 (4.60–5.53) | 1.28 (1.00–1.32) | 3.74× |

| Engine | Ready-to-keys median seconds | First tile median seconds | Peak service-tree RSS range MiB | Peak FDs range |
| --- | --- | --- | --- | --- |
| python | 0.410 | 0.100 | 461.7–579.0 | 49–57 |
| rust | 0.022 | 0.073 | 112.5–190.5 | 10–19 |

Use the per-workload results; they do not establish a universal speedup. Cache representations differ (compressed per-process Python versus uncompressed shared native), so cache-on results are a deployment comparison rather than identical internal cache behavior. The cache-off rows remove application cache differences, while GDAL and the OS still cache reads. Raw samples, process commands, resource peaks and range traffic are retained in `benchmark.json`.
