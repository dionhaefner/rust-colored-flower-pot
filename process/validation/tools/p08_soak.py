"""Sustained mixed real-raster workload with bounded cache and recovery checks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import collections
import json
import time

from common import ROOT, write_json
from p08_benchmark import workloads
from p08_runtime import DATABASE, Service, fetch, image_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--out', default='process/validation/artifacts/p08-soak')
    args = parser.parse_args(); out = ROOT / args.out; out.mkdir(parents=True, exist_ok=False)
    paths = list(workloads().values())
    # Different output grids force cache eviction rather than measuring one hot item.
    paths = [path + f'&tile_size=[{size},{size}]' for path in paths for size in [128, 192, 256, 384]]
    with Service(DATABASE, out, cache=2 * 1024**2, extra={'TC_RASTER_QUEUE_CAPACITY': '8'}) as service:
        expected = {}
        for path in paths:
            status, body = fetch(service.base, path); assert status == 200
            expected[path] = image_hash(body)
        baseline = service.snapshot(); before = service.metrics()
        start = time.monotonic(); counts = collections.Counter(); snapshots = []
        total = 0
        def request(index):
            path = paths[index % len(paths)]
            status, body = fetch(service.base, path)
            assert status in [200, 503], (status, body[:200])
            if status == 200:
                assert image_hash(body) == expected[path], path
            return status
        with ThreadPoolExecutor(12) as pool:
            while time.monotonic() - start < args.seconds:
                counts.update(pool.map(request, range(total, total + 48)))
                total += 48
                if not snapshots or time.monotonic() - snapshots[-1]['seconds'] >= 5:
                    snapshots.append(dict(service.snapshot(), metrics=service.metrics()))
        # Invalid requests and a subsequent real raster must not poison the service.
        failures = {path: fetch(service.base, path)[0] for path in [
            '/singleband/missing/red/preview.png', '/compute/lagoon/preview.png?expression=__import__(1)',
            '/singleband/lagoon/red/preview.png?tile_size=[0,0]']}
        assert all(400 <= status < 500 for status in failures.values()), failures
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            after = service.metrics()
            if after['in_flight'] == 0: break
            time.sleep(.05)
        status, body = fetch(service.base, paths[0]); assert status == 200 and image_hash(body) == expected[paths[0]]
        after = service.metrics(); final = service.snapshot(); resources = service.resources()
        assert after['in_flight'] == 0 and after['submitted'] == after['completed'] + after['failed'], after
        assert resources['peak_rss'] < 512 * 1024**2, resources
        assert final['fds'] <= baseline['fds'] + 12, (baseline, final)
        assert all(s['metrics']['peak_in_flight'] <= 3 and s['metrics']['cache_bytes'] <= 2*1024**2 for s in snapshots), snapshots
        # Bound late growth after worker/native allocator warmup. This is an
        # observed resource envelope, not proof against a slower long-term leak.
        warm=snapshots[len(snapshots)//3:2*len(snapshots)//3]
        late=snapshots[2*len(snapshots)//3:]
        late_growth=max(s['rss'] for s in late)-max(s['rss'] for s in warm)
        assert late_growth<32*1024**2,late_growth
        report = dict(passed=True, elapsed_seconds=time.monotonic()-start, requested_seconds=args.seconds,
            concurrency=12, distinct_grids=len(paths), status_counts=dict(counts),
            invalid_requests=failures, baseline=baseline, final=final, resources=resources,
            metrics_before=before, metrics_after=after, snapshots=snapshots,late_peak_growth_bytes=late_growth,
            limits=dict(rss_bytes=512*1024**2,workers=3,queue=8,cache_bytes=2*1024**2),
            scope='Three-minute sustained local workload; this is not a multi-day production leak or cloud qualification.')
        write_json(out/'report.json', report); print(json.dumps({k:v for k,v in report.items() if k != 'snapshots'},indent=2))


if __name__ == '__main__': main()
