# Measured performance — Linux x86_64

Sequential engine trials, three CPU affinity cores, three sync Gunicorn workers versus three native raster workers; native threads and BLAS capped. Primary cache-off comparison; cache-on uses total 64 MiB logical budget (Python compressed per worker, Rust uncompressed shared). OS page cache is warm, not dropped. Cold means fresh service process and first raster request, not cold disk. RSS is sum of service process-tree RSS including shared pages; 20ms samples. Loopback range server adds 2ms per GET. No external network performance claim.

Three trials per engine/access/cache configuration, 36 timed requests per workload/concurrency in each trial. Each response is checked for exact decoded RGBA against the oracle. Throughput includes client dispatch and image checking; latency samples end when the body is received. Each workload is warmed before timing. `geographic-dem` always uses the local DEM, including range-labeled runs. Fresh-process latency is measured on the first singleband tile only. No cold-disk, WAN or cloud startup claim.

Trial ranges describe observed variation, not confidence intervals. Sum-of-RSS double-counts shared pages in the multi-process Python server; it is not unique resident memory or PSS. The identical 1.5 GiB service-tree RSS guard is checked, not a kernel-enforced cgroup limit. Three CPU cores are affinity-constrained for each service; the load generator/range server run outside that affinity.

| Access | App cache | Workload | Clients | Python median latency ms (trial range) | Rust median latency ms (trial range) | Python / Rust latency |
| --- | --- | --- | --- | --- | --- | --- |
| local | off | compute-tile | 1 | 77.96 (77.75–78.51) | 93.98 (88.82–103.86) | 0.83× |
| local | off | compute-tile | 3 | 82.48 (82.04–82.77) | 84.38 (81.91–86.08) | 0.98× |
| local | off | geographic-dem | 1 | 71.14 (71.02–77.82) | 59.57 (58.12–61.39) | 1.19× |
| local | off | geographic-dem | 3 | 70.24 (68.62–73.54) | 111.23 (110.94–111.23) | 0.63× |
| local | off | high-zoom | 1 | 21.33 (20.94–22.07) | 10.41 (9.56–11.63) | 2.05× |
| local | off | high-zoom | 3 | 13.22 (13.16–13.77) | 7.41 (7.33–7.46) | 1.78× |
| local | off | preview | 1 | 728.56 (722.75–728.98) | 481.86 (480.84–524.66) | 1.51× |
| local | off | preview | 3 | 736.27 (734.42–737.44) | 1408.32 (1371.80–1411.60) | 0.52× |
| local | off | rgb-tile | 1 | 124.05 (123.75–124.58) | 46.24 (46.02–46.40) | 2.68× |
| local | off | rgb-tile | 3 | 132.12 (130.53–133.70) | 120.18 (118.65–120.65) | 1.10× |
| local | off | singleband-tile | 1 | 50.12 (49.47–50.29) | 41.69 (41.48–41.72) | 1.20× |
| local | off | singleband-tile | 3 | 48.11 (47.82–52.66) | 41.80 (41.35–41.90) | 1.15× |
| local | 64 MiB | compute-tile | 1 | 4.30 (4.30–4.50) | 6.49 (5.49–6.52) | 0.66× |
| local | 64 MiB | compute-tile | 3 | 4.82 (4.76–5.04) | 6.40 (5.97–7.93) | 0.75× |
| local | 64 MiB | geographic-dem | 1 | 4.65 (4.62–8.02) | 0.71 (0.71–0.72) | 6.54× |
| local | 64 MiB | geographic-dem | 3 | 5.38 (4.58–5.70) | 1.24 (1.18–1.31) | 4.35× |
| local | 64 MiB | high-zoom | 1 | 3.61 (3.60–3.62) | 0.71 (0.70–0.72) | 5.06× |
| local | 64 MiB | high-zoom | 3 | 3.93 (3.85–4.38) | 1.19 (0.90–1.36) | 3.31× |
| local | 64 MiB | preview | 1 | 11.39 (11.30–13.27) | 2.27 (2.24–2.47) | 5.02× |
| local | 64 MiB | preview | 3 | 11.74 (11.57–11.80) | 2.81 (2.66–3.08) | 4.18× |
| local | 64 MiB | rgb-tile | 1 | 11.75 (11.71–11.88) | 2.47 (2.39–2.50) | 4.75× |
| local | 64 MiB | rgb-tile | 3 | 12.11 (12.05–12.17) | 3.10 (3.01–3.37) | 3.91× |
| local | 64 MiB | singleband-tile | 1 | 5.05 (4.45–5.27) | 0.79 (0.77–0.82) | 6.40× |
| local | 64 MiB | singleband-tile | 3 | 4.87 (4.66–7.81) | 1.13 (1.12–1.17) | 4.29× |
| range | off | compute-tile | 1 | 77.06 (76.31–77.14) | 92.81 (90.81–96.70) | 0.83× |
| range | off | compute-tile | 3 | 82.80 (82.01–83.74) | 80.10 (76.70–82.47) | 1.03× |
| range | off | geographic-dem | 1 | 75.75 (71.05–75.97) | 57.91 (57.90–60.92) | 1.31× |
| range | off | geographic-dem | 3 | 68.87 (67.40–71.03) | 111.10 (110.84–111.16) | 0.62× |
| range | off | high-zoom | 1 | 18.46 (18.21–23.59) | 8.93 (7.24–9.72) | 2.07× |
| range | off | high-zoom | 3 | 13.47 (13.25–13.54) | 7.53 (7.03–7.76) | 1.79× |
| range | off | preview | 1 | 721.28 (720.70–722.29) | 517.51 (478.21–520.47) | 1.39× |
| range | off | preview | 3 | 731.06 (731.00–754.39) | 1407.76 (1389.11–1416.38) | 0.52× |
| range | off | rgb-tile | 1 | 120.51 (119.89–121.22) | 44.87 (41.53–46.22) | 2.69× |
| range | off | rgb-tile | 3 | 128.07 (126.95–136.51) | 113.72 (107.32–115.18) | 1.13× |
| range | off | singleband-tile | 1 | 47.85 (47.83–51.85) | 40.83 (39.99–41.19) | 1.17× |
| range | off | singleband-tile | 3 | 46.14 (45.22–57.12) | 41.29 (40.45–41.34) | 1.12× |
| range | 64 MiB | compute-tile | 1 | 4.51 (4.29–4.69) | 6.52 (6.50–6.74) | 0.69× |
| range | 64 MiB | compute-tile | 3 | 4.80 (4.64–4.89) | 5.83 (5.68–6.23) | 0.82× |
| range | 64 MiB | geographic-dem | 1 | 3.97 (3.81–6.40) | 0.71 (0.70–0.72) | 5.58× |
| range | 64 MiB | geographic-dem | 3 | 4.66 (4.04–4.75) | 1.22 (0.87–1.30) | 3.81× |
| range | 64 MiB | high-zoom | 1 | 4.11 (3.63–5.23) | 0.71 (0.70–0.72) | 5.79× |
| range | 64 MiB | high-zoom | 3 | 4.21 (3.71–5.47) | 1.11 (0.86–1.26) | 3.80× |
| range | 64 MiB | preview | 1 | 11.40 (11.36–11.44) | 1.65 (1.62–1.68) | 6.93× |
| range | 64 MiB | preview | 3 | 11.73 (11.68–11.78) | 2.13 (2.02–2.29) | 5.52× |
| range | 64 MiB | rgb-tile | 1 | 11.95 (11.91–12.01) | 1.81 (1.77–1.88) | 6.61× |
| range | 64 MiB | rgb-tile | 3 | 12.37 (12.33–12.59) | 2.44 (2.36–2.57) | 5.08× |
| range | 64 MiB | singleband-tile | 1 | 5.24 (4.84–5.24) | 0.76 (0.74–0.76) | 6.93× |
| range | 64 MiB | singleband-tile | 3 | 4.62 (4.59–5.07) | 1.06 (0.99–1.16) | 4.35× |

| Engine | Ready-to-keys median seconds | First tile median seconds | Peak service-tree RSS range MiB | Peak FDs range |
| --- | --- | --- | --- | --- |
| python | 0.414 | 0.098 | 462.7–579.9 | 49–57 |
| rust | 0.021 | 0.071 | 113.7–189.2 | 10–18 |

Use the per-workload results; they do not establish a universal speedup. Cache representations differ (compressed per-process Python versus uncompressed shared native), so cache-on results are a deployment comparison rather than identical internal cache behavior. The cache-off rows remove application cache differences, while GDAL and the OS still cache reads. Raw samples, process commands, resource peaks and range traffic are retained in `benchmark.json`.
