"""Fair local production-WSGI/native benchmark; no implicit cloud or downloads."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import platform
import shutil
import sqlite3
import time
from urllib.parse import urlencode

import numpy as np
import psutil
import rasterio
from common import COMMIT, ROOT, write_json
from p08_runtime import DATABASE, Service, fetch, image_hash
from range_server import RangeServer


def workloads(scene='lagoon'):
    rgb = urlencode(dict(r='red', g='green', b='blue', r_range='[0,3000]', g_range='[0,3000]', b_range='[0,3000]'))
    compute = urlencode(dict(expression='(v1-v2)/(v1+v2)', v1='nir', v2='red', stretch_range='[-1,1]', colormap='viridis'))
    return {
        'singleband-tile': f'/singleband/{scene}/red/12/2188/1466.png?stretch_range=[0,3000]',
        'rgb-tile': f'/rgb/{scene}/12/2188/1466.png?{rgb}',
        'compute-tile': f'/compute/{scene}/12/2188/1466.png?{compute}',
        'preview': f'/rgb/{scene}/preview.png?{rgb}',
        'high-zoom': f'/singleband/{scene}/red/16/35008/23456.png?stretch_range=[0,3000]',
        'geographic-dem': '/singleband/dolomites/elevation/preview.png?colormap=terrain',
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=str, default='validation/artifacts/p08-benchmark')
    p.add_argument('--repetitions', type=int, default=3)
    p.add_argument('--requests', type=int, default=36)
    a = p.parse_args(); out = ROOT / a.out; out.mkdir(parents=True, exist_ok=False)
    cpus = psutil.Process().cpu_affinity()[:3]
    assert len(cpus) == 3
    report = dict(reference_commit=COMMIT, platform=platform.platform(), cpu_affinity=cpus,
                  cpu=platform.processor(), gdal=rasterio.__gdal_version__, repetitions=a.repetitions,
                  conditions='Sequential engine trials, three CPU affinity cores, three sync Gunicorn workers versus three native raster workers; native threads and BLAS capped. Primary cache-off comparison; cache-on uses total 64 MiB logical budget (Python compressed per worker, Rust uncompressed shared). OS page cache is warm, not dropped. Cold means fresh service process and first raster request, not cold disk. RSS is sum of service process-tree RSS including shared pages; 20ms samples. Loopback range server adds 2ms per GET. No external network performance claim.',
                  runs=[])
    golden = {}
    with_range = RangeServer(ROOT / '.cache/corpus', 0, delay_seconds=.002)
    try:
        database = out / 'remote.sqlite'; shutil.copy2(DATABASE, database)
        with sqlite3.connect(database) as conn:
            for band in ['red', 'green', 'blue', 'nir']:
                conn.execute('UPDATE datasets SET path=? WHERE scene=? AND band=?',
                             (f'http://127.0.0.1:{with_range.port}/subsets/lagoon-{band}.tif', 'lagoon', band))
        for repetition in range(a.repetitions):
            for access, db in [('local', DATABASE), ('range', database)]:
                for cache in [0, 64 * 1024**2]:
                    # Alternate order to reduce systematic thermal/page-cache bias.
                    for engine in (['python', 'rust'] if repetition % 2 == 0 else ['rust', 'python']):
                        label = f'{repetition}-{access}-{cache}-{engine}'
                        traffic_before = with_range.snapshot()
                        with Service(db, out / label, engine, cache, cpus) as service:
                            def request(path):
                                start = time.perf_counter(); status, body = fetch(service.base, path)
                                elapsed = time.perf_counter() - start
                                assert status == 200, (engine, path, status, body[:200])
                                digest = image_hash(body)
                                if path in golden:
                                    assert golden[path] == digest, (engine, path, 'decoded pixels differ')
                                else:
                                    golden[path] = digest
                                return elapsed
                            cold = request(workloads()['singleband-tile'])
                            measurements = []
                            for name, path in workloads().items():
                                # Warm every process/worker before timing each workload.
                                for _ in range(9): request(path)
                                for concurrency in [1, 3]:
                                    before = with_range.snapshot(); started = time.perf_counter()
                                    with ThreadPoolExecutor(concurrency) as pool:
                                        samples = list(pool.map(lambda _: request(path), range(a.requests)))
                                    elapsed = time.perf_counter() - started
                                    after = with_range.snapshot()
                                    measurements.append(dict(name=name, path=path, concurrency=concurrency,
                                        requests=a.requests, seconds=elapsed, requests_per_second=a.requests / elapsed,
                                        latency_seconds=dict(p50=float(np.percentile(samples, 50)), p95=float(np.percentile(samples, 95)), p99=float(np.percentile(samples, 99))),
                                        samples_seconds=samples, range_traffic={k: after[k] - before[k] for k in before}))
                            resources = service.resources()
                            assert resources['peak_rss'] < 1536 * 1024**2, resources
                            record = dict(engine=engine, access=access, cache_bytes=cache, repetition=repetition,
                                startup_seconds=service.startup_seconds, first_raster_seconds=cold,
                                resources=resources, command=service.command, measurements=measurements)
                        traffic_after = with_range.snapshot()
                        record['total_range_traffic'] = {k: traffic_after[k] - traffic_before[k] for k in traffic_before}
                        report['runs'].append(record); write_json(out / 'report.json', report)
                        print(label, resources, flush=True)
        report['passed'] = True; report['decoded_image_hashes'] = golden
        write_json(out / 'report.json', report)
    finally:
        with_range.close()


if __name__ == '__main__': main()
