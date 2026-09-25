"""Summarize repeated measurements without hiding trial spread or cache modes."""
import collections
import json
import statistics
from common import ROOT, write_json


def main():
    report=json.loads((ROOT/'validation/artifacts/p08-benchmark/report.json').read_text())
    assert report['passed'] and len(report['runs'])==24
    groups=collections.defaultdict(list)
    resources=collections.defaultdict(list)
    for run in report['runs']:
        resources[run['engine']].append(run)
        for m in run['measurements']:
            groups[(run['access'],run['cache_bytes'],m['name'],m['concurrency'],run['engine'])].append(m)
    rows=[]
    lines=['# Measured performance — Linux x86_64','',report['conditions'],'',
           'Three trials per engine/access/cache configuration, 36 timed requests per workload/concurrency in each trial. Each response is checked for exact decoded RGBA against the oracle. Throughput includes client dispatch and image checking; latency samples end when the body is received. Each workload is warmed before timing. `geographic-dem` always uses the local DEM, including range-labeled runs. Fresh-process latency is measured on the first singleband tile only. No cold-disk, WAN or cloud startup claim.','',
           'Trial ranges describe observed variation, not confidence intervals. Sum-of-RSS double-counts shared pages in the multi-process Python server; it is not unique resident memory or PSS. The identical 1.5 GiB service-tree RSS guard is checked, not a kernel-enforced cgroup limit. Three CPU cores are affinity-constrained for each service; the load generator/range server run outside that affinity.','',
           '| Access | App cache | Workload | Clients | Python median latency ms (trial range) | Rust median latency ms (trial range) | Python / Rust latency |',
           '| --- | --- | --- | --- | --- | --- | --- |']
    for key in sorted({k[:4] for k in groups}):
        a=groups[(*key,'python')];b=groups[(*key,'rust')]
        def summary(samples):
            medians=[s['latency_seconds']['p50']*1000 for s in samples]
            return dict(p50_ms=statistics.median(medians),p50_trial_min_ms=min(medians),p50_trial_max_ms=max(medians),
                p95_ms=statistics.median(s['latency_seconds']['p95']*1000 for s in samples),
                p99_ms=statistics.median(s['latency_seconds']['p99']*1000 for s in samples),
                requests_per_second=statistics.median(s['requests_per_second'] for s in samples),
                total_range_requests=sum(s['range_traffic']['range_requests'] for s in samples),
                total_range_bytes=sum(s['range_traffic']['bytes_sent'] for s in samples))
        py,rs=summary(a),summary(b);ratio=py['p50_ms']/rs['p50_ms']
        rows.append(dict(access=key[0],cache_bytes=key[1],workload=key[2],concurrency=key[3],python=py,rust=rs,python_over_rust_p50=ratio))
        lines.append(f'| {key[0]} | {"off" if key[1]==0 else "64 MiB"} | {key[2]} | {key[3]} | {py["p50_ms"]:.2f} ({py["p50_trial_min_ms"]:.2f}–{py["p50_trial_max_ms"]:.2f}) | {rs["p50_ms"]:.2f} ({rs["p50_trial_min_ms"]:.2f}–{rs["p50_trial_max_ms"]:.2f}) | {ratio:.2f}× |')
    measured={}
    lines.extend(['','| Engine | Ready-to-keys median seconds | First tile median seconds | Peak service-tree RSS range MiB | Peak FDs range |','| --- | --- | --- | --- | --- |'])
    for engine,runs in resources.items():
        measured[engine]=dict(startup_median_seconds=statistics.median(r['startup_seconds'] for r in runs),first_raster_median_seconds=statistics.median(r['first_raster_seconds'] for r in runs),peak_rss_min=min(r['resources']['peak_rss'] for r in runs),peak_rss_max=max(r['resources']['peak_rss'] for r in runs),peak_fds_min=min(r['resources']['peak_fds'] for r in runs),peak_fds_max=max(r['resources']['peak_fds'] for r in runs))
        v=measured[engine];lines.append(f'| {engine} | {v["startup_median_seconds"]:.3f} | {v["first_raster_median_seconds"]:.3f} | {v["peak_rss_min"]/1024**2:.1f}–{v["peak_rss_max"]/1024**2:.1f} | {v["peak_fds_min"]}–{v["peak_fds_max"]} |')
    lines.extend(['','Use the per-workload results; they do not establish a universal speedup. Cache representations differ (compressed per-process Python versus uncompressed shared native), so cache-on results are a deployment comparison rather than identical internal cache behavior. The cache-off rows remove application cache differences, while GDAL and the OS still cache reads. Raw samples, process commands, resource peaks and range traffic are retained in `benchmark.json`.',''])
    (ROOT/'validation/evidence/p08/PERFORMANCE.md').write_text('\n'.join(lines))
    write_json(ROOT/'validation/evidence/p08/performance-summary.json',dict(rows=rows,resources=measured,conditions=report['conditions']))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    selected=[r for r in rows if r['access']=='local' and r['cache_bytes']==0 and r['concurrency']==3]
    y=np.arange(len(selected));fig,ax=plt.subplots(figsize=(9,4.5))
    for engine,shift,color in [('python',-.18,'#3b6b9a'),('rust',.18,'#b95632')]:
        med=np.array([r[engine]['p50_ms'] for r in selected])
        lower=med-np.array([r[engine]['p50_trial_min_ms'] for r in selected])
        upper=np.array([r[engine]['p50_trial_max_ms'] for r in selected])-med
        ax.barh(y+shift,med,height=.34,label='Gunicorn / frozen Python' if engine=='python' else 'Native Rust',color=color,xerr=np.stack([lower,upper]),capsize=3)
    ax.set_yticks(y,[r['workload'] for r in selected]);ax.set_xscale('log')
    ax.set_xlabel('Median response latency (ms, log scale); error bars show three-trial range')
    ax.set_title('Local rasters · application cache off · 3 clients · equal 3-core affinity')
    ax.legend();ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True);fig.tight_layout()
    fig.savefig(ROOT/'validation/evidence/p08/performance.png',dpi=160);plt.close(fig)


if __name__=='__main__':main()
