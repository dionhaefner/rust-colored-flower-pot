"""Summarize viewport completion separately from isolated request latency."""
import argparse
import collections
import json
from pathlib import Path
import shutil
import statistics
from common import ROOT, write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',default='validation/artifacts/p08-navigation-final/report.json');p.add_argument('--out',default='validation/evidence/p08/navigation');a=p.parse_args()
    report=json.loads((ROOT/a.input).read_text());assert report['passed'] and len(report['runs'])==36
    out=ROOT/a.out;out.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/a.input,out/'report.json')
    groups=collections.defaultdict(list)
    for r in report['runs']:groups[r['users'],r['mode'],r['engine']].append(r)
    rows=[]
    for users in [1,4,8]:
        for mode in ['singleband','rgb']:
            row=dict(users=users,mode=mode)
            for engine in ['python','rust']:
                runs=groups[users,mode,engine];assert len(runs)==3
                row[engine]={stage:{metric:statistics.median(r[stage][metric] for r in runs) for metric in ['p50_ms','p95_ms','p99_ms']} for stage in ['first_view','pan','tile']}
                row[engine]['pan_p50_trial_range_ms']=[min(r['pan']['p50_ms'] for r in runs),max(r['pan']['p50_ms'] for r in runs)]
                row[engine]['peak_rss_bytes']=max(r['resources']['peak_rss'] for r in runs)
                row[engine]['cpu_seconds_median']=statistics.median(r['cpu_seconds'] for r in runs)
                row[engine]['requests']=sum(r['requests'] for r in runs)
                row[engine]['failures']=sum(r['failures'] for r in runs)
            row['pan_speedup']=row['python']['pan']['p50_ms']/row['rust']['pan']['p50_ms']
            row['first_view_speedup']=row['python']['first_view']['p50_ms']/row['rust']['first_view']['p50_ms']
            rows.append(row)
    cov=list(report['coverage'].values())
    write_json(out/'summary.json',dict(rows=rows,conditions=report['conditions'],unique_tile_urls=len(cov),minimum_opaque_fraction=min(cov),mean_opaque_fraction=statistics.mean(cov),requests=sum(r['requests'] for r in report['runs']),failures=sum(r['failures'] for r in report['runs'])))
    lines=['# Map navigation: singleband and RGB','',
        'Primary metric: time from requesting newly visible tiles until the last response arrives for a 1024×768 map view. A session retains tiles already downloaded; horizontal pans request three new tiles, vertical pans four. Each session has up to six in-flight requests. First-view timing covers twelve tiles from a fresh service, after `/keys` readiness. This measures server tile delivery, not browser animation, compositing or network latency.','',
        'Three repetitions per engine/mode/session count, alternating engine order. Table values are medians of per-trial statistics, rounded to milliseconds; raw samples and trial ranges remain in JSON. A single session has six measured pans per trial, so its tail estimate is based on few view events. More users have spatially offset tracks with natural overlap; this is one specified browsing workload, not a universal traffic distribution.','',
        '| Users | Mode | Python pan p50 / p95 ms | Rust pan p50 / p95 ms | Pan speedup | Python / Rust first-view p50 ms |',
        '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        py,rs=row['python'],row['rust'];lines.append(f'| {row["users"]} | {row["mode"]} | {py["pan"]["p50_ms"]:.0f} / {py["pan"]["p95_ms"]:.0f} | {rs["pan"]["p50_ms"]:.0f} / {rs["pan"]["p95_ms"]:.0f} | {row["pan_speedup"]:.2f}× | {py["first_view"]["p50_ms"]:.0f} / {rs["first_view"]["p50_ms"]:.0f} |')
    lines.extend(['','## Conditions and validity','',
        '- Full, unmodified Sentinel-2 RGB COGs with original overviews and pinned source hashes; zoom 13. Every measured image is checked against frozen Python. All measured tiles are at least 95% opaque, avoiding empty-tile speed inflation.',
        '- Three affinity-constrained CPU cores for each engine. Three Gunicorn sync processes versus three native raster workers. GDAL cache is 32 MiB per Python process versus 96 MiB shared native: equal 96 MiB aggregate budgets. Application caches total 64 MiB each, compressed/per-process in Python and uncompressed/shared in Rust.',
        '- Local raster access and warm OS disk cache. No WAN, real S3, cold-disk, browser paint or cancelled-request claim. The client pauses 100 ms after each completed view, then pans; this does not measure continuous-drag animation.',
        '- Native queue capacity is configured to 256 for concurrent tile bursts. This is a capacity setting, not a changed default; no failed requests are retried or removed. CPU time and RSS peaks are retained; RSS sums shared Python pages and is not PSS.',
        '- Initial bounding-box tracks hit substantial nodata and were rejected as unrepresentative. Their report remains in `validation/artifacts/p08-navigation`. The first valid-track attempt briefly overlapped that pilot and was stopped; it remains in `p08-navigation-valid`. Only the isolated `p08-navigation-final` run supplies these figures.',
        '',f'Observed {len(cov)} distinct tile URLs, minimum opaque coverage {min(cov):.1%}; {sum(r["requests"] for r in report["runs"])} requests and {sum(r["failures"] for r in report["runs"])} HTTP failures. Raw commands, paths, image hashes, timings, CPU/RSS and native queue/cache metrics are in [report.json](report.json).',
        '','## Reproduce','',
        'Run alone on the host, with the frozen environment, full cached corpus and final native candidate available:',
        '', '```sh','PYTHONPATH=.cache/reference .venv-reference/bin/python validation/tools/p08_navigation.py --out validation/artifacts/NEW-navigation',
        '.venv-reference/bin/python validation/tools/p08_navigation_report.py --input validation/artifacts/NEW-navigation/report.json --out validation/evidence/NEW-navigation','```','',
        'This follow-up does not modify the native implementation or replace/reseal the earlier P08 archives. It answers map-navigation serving performance with corrected aggregate cache budgets; older repeated-tile/expression microbenchmarks remain separate evidence.',''])
    (out/'README.md').write_text('\n'.join(lines))
    print('\n'.join(lines[6:15]))


if __name__=='__main__':main()
