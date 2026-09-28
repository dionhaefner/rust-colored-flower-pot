"""Measure newly visible map tiles: one and concurrent browsing sessions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlencode

import numpy as np
from PIL import Image
import psutil
import rasterio
import terracotta as tc
from common import ROOT, COMMIT, sha256, write_json
import p08_runtime
from p08_runtime import BINARY, Service, fetch, image_hash


def tracks(users,mode):
    query=urlencode(dict(r='red',g='green',b='blue',r_range='[0,3000]',g_range='[0,3000]',b_range='[0,3000]'))
    offsets=[(0,0),(1,0),(2,0),(3,0),(4,0),(4,1),(4,2)]
    sessions=[]
    for user in range(users):
        # Stay in the real valid Sentinel footprint, not merely its bounding
        # rectangle (much of that rectangle is nodata). Original scene pixels.
        x0=4381+(user%2);y0=2917+user*3
        seen=set();views=[]
        for step,(dx,dy) in enumerate(offsets):
            tiles=[(x0+dx+x,y0+dy+y) for y in range(3) for x in range(4)]
            paths=[]
            for x,y in tiles:
                if (x,y) in seen:continue
                seen.add((x,y))
                paths.append(f'/singleband/scene/red/13/{x}/{y}.png?stretch_range=[0,3000]' if mode=='singleband' else f'/rgb/scene/13/{x}/{y}.png?{query}')
            views.append(dict(step=step,tiles=tiles,paths=paths))
        sessions.append(views)
    return sessions


def distribution(values):
    return {f'p{n}_ms':float(np.percentile(values,n))*1000 for n in [50,95,99]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default='validation/artifacts/p08-navigation')
    p.add_argument('--rust-binary',default=str(BINARY),help='Exact candidate executable to launch and hash')
    p.add_argument('--repetitions',type=int,default=3)
    p.add_argument('--users',type=int,nargs='+',default=[1,4,8])
    p.add_argument('--native-env',action='append',default=[],metavar='KEY=VALUE')
    p.add_argument('--allow-mismatch',action='store_true',help='Exploratory timing only; never mark a mismatched run passed')
    p.add_argument('--compare-pixels',action='store_true',help='Retain image bodies for a separate per-pixel comparison after timing')
    p.add_argument('--rust-gdal-mib',type=int,default=96)
    p.add_argument('--python-gdal-mib-per-worker',type=int,default=32)
    p.add_argument('--rust-app-mib',type=int,default=64)
    p.add_argument('--rust-source-cache-mib',type=int,default=0)
    p.add_argument('--python-app-mib',type=int,default=64)
    p.add_argument('--source-dir',default='.cache/corpus')
    args=p.parse_args();p08_runtime.BINARY=Path(args.rust_binary).resolve();assert p08_runtime.BINARY.is_file()
    out=ROOT/args.out;out.mkdir(parents=True,exist_ok=False)
    native_extra=dict(item.split('=',1) for item in args.native_env)
    assert all(key.startswith(('TC_','GDAL_')) for key in native_extra)
    assert args.rust_gdal_mib+args.rust_app_mib+args.rust_source_cache_mib==3*args.python_gdal_mib_per_worker+args.python_app_mib
    if args.rust_source_cache_mib:native_extra['TC_SOURCE_BLOCK_CACHE_BYTES']=str(args.rust_source_cache_mib*1024**2)
    source_dir=(ROOT/args.source_dir).resolve()
    assert source_dir.is_dir()
    assets=json.loads((ROOT/'validation/reference/assets.json').read_text())['assets']
    database=out/'catalog.sqlite';driver=tc.get_driver(database);driver.create(['scene','band'])
    sources=[]
    for band in ['red','green','blue']:
        asset=next(a for a in assets if a['id']=='s2-'+band);path=source_dir/('s2-'+band+'.tif')
        digest=sha256(path)
        if source_dir==(ROOT/'.cache/corpus').resolve():assert digest==asset['sha256']
        driver.insert(('scene',band),str(path),metadata=driver.compute_metadata(str(path),max_shape=[512,512]))
        with rasterio.open(path) as src:sources.append(dict(path=str(path.relative_to(ROOT)),sha256=digest,shape=list(src.shape),overviews=src.overviews(1),crs=str(src.crs)))
    cpus=psutil.Process().cpu_affinity()[:3];assert len(cpus)==3
    report=dict(reference_commit=COMMIT,native_sha256=sha256(p08_runtime.BINARY),sources=sources,cpu_affinity=cpus,
        conditions=dict(viewport=[1024,768],tile_size=256,zoom=13,requests_in_flight_per_user=6,pan_pause_seconds=.1,views_per_session=7,
            rust_gdal_cache_mib=args.rust_gdal_mib,python_gdal_cache_mib=3*args.python_gdal_mib_per_worker,
            rust_app_cache_mib=args.rust_app_mib,rust_source_cache_mib=args.rust_source_cache_mib,python_app_cache_mib=args.python_app_mib,
            aggregate_cache_mib=args.rust_gdal_mib+args.rust_app_mib+args.rust_source_cache_mib,
            rust_raster_workers=int(native_extra.get('TC_RASTER_WORKERS','3')),python_gunicorn_workers=3,
            rust_queue_capacity=256,native_extra=native_extra,access=f'local {source_dir.relative_to(ROOT)} rasters, warm OS disk cache; fresh service for every trial',
            scope='Server-side newly visible tile completion, not browser animation/paint or WAN latency. Sessions keep downloaded tiles. Spatially offset concurrent tracks have natural overlap. No retries or cancelled requests. Python cache compressed per process; native uncompressed shared. RSS sums shared Python pages.'),runs=[])
    golden={};coverage={};mismatches=set();golden_lock=threading.Lock()
    reference_bodies={};native_bodies={}
    for trial in range(args.repetitions):
        for users in args.users:
            for mode in ['singleband','rgb']:
                routes=tracks(users,mode)
                for engine in (['python','rust'] if trial%2==0 else ['rust','python']):
                    label=f'{trial}-{users}-{mode}-{engine}'
                    extra=dict(GDAL_CACHEMAX=str(args.rust_gdal_mib if engine=='rust' else args.python_gdal_mib_per_worker),TC_RASTER_QUEUE_CAPACITY='256')
                    if engine=='rust':extra.update(native_extra)
                    app_mib=args.rust_app_mib if engine=='rust' else args.python_app_mib
                    with Service(database,out/label,engine,app_mib*1024**2,cpus,extra) as service:
                        barrier=threading.Barrier(users)
                        cpu_start=sum(p.cpu_times().user+p.cpu_times().system for p in [service.root,*service.root.children(recursive=True)])
                        def session(user):
                            views=[]
                            with ThreadPoolExecutor(6) as requests:
                                barrier.wait()
                                for view in routes[user]:
                                    def request(path):
                                        started=time.perf_counter();status,body=fetch(service.base,path);received=time.perf_counter()
                                        row=dict(path=path,status=status,latency_seconds=received-started,received=received,bytes=len(body))
                                        if status==200:
                                            digest=image_hash(body)
                                            with golden_lock:
                                                if path not in golden:
                                                    assert engine=='python','golden must come from Python'
                                                    golden[path]=digest
                                                    if args.compare_pixels:reference_bodies[path]=body
                                                    with Image.open(io.BytesIO(body)) as im:
                                                        a=np.asarray(im.convert('RGBA'));coverage[path]=float(np.count_nonzero(a[:,:,3]))/(a.shape[0]*a.shape[1])
                                                assert coverage[path]>=.95,(path,'insufficient real-image coverage',coverage[path])
                                                if engine=='rust' and args.compare_pixels and path not in native_bodies:native_bodies[path]=body
                                                if digest!=golden[path]:
                                                    mismatches.add(path)
                                                    if not args.allow_mismatch:raise AssertionError((engine,path,'decoded pixels differ'))
                                        return row
                                    started=time.perf_counter();responses=list(requests.map(request,view['paths']))
                                    elapsed=max(r['received'] for r in responses)-started
                                    for r in responses:r.pop('received')
                                    views.append(dict(user=user,step=view['step'],seconds=elapsed,responses=responses,complete=all(r['status']==200 for r in responses)))
                                    time.sleep(.1)
                            return views
                        started=time.perf_counter()
                        with ThreadPoolExecutor(users) as sessions:views=[v for group in sessions.map(session,range(users)) for v in group]
                        elapsed=time.perf_counter()-started
                        cpu_end=sum(p.cpu_times().user+p.cpu_times().system for p in [service.root,*service.root.children(recursive=True)])
                        responses=[r for v in views for r in v['responses']]
                        row=dict(trial=trial,users=users,mode=mode,engine=engine,command=service.command,views=views,
                            first_view=distribution([v['seconds'] for v in views if v['step']==0]),
                            pan=distribution([v['seconds'] for v in views if v['step']>0]),
                            tile=distribution([r['latency_seconds'] for r in responses]),
                            seconds=elapsed,cpu_seconds=cpu_end-cpu_start,resources=service.resources(),
                            requests=len(responses),failures=sum(r['status']!=200 for r in responses),
                            native_metrics=service.metrics() if engine=='rust' else None)
                    report['runs'].append(row);report['coverage']=coverage;report['decoded_image_hashes']=golden;report['mismatch_paths']=sorted(mismatches)
                    write_json(out/'report.json',report)
                    print(label,'first',round(row['first_view']['p50_ms']),'pan',round(row['pan']['p50_ms']),'p95',round(row['pan']['p95_ms']),'failures',row['failures'],flush=True)
    if args.compare_pixels:
        comparisons={}
        for path,reference in reference_bodies.items():
            with Image.open(io.BytesIO(reference)) as image:
                expected=np.asarray(image.convert('RGBA')).astype(np.int16)
            with Image.open(io.BytesIO(native_bodies[path])) as image:
                observed=np.asarray(image.convert('RGBA')).astype(np.int16)
            delta=np.abs(expected-observed)
            rgb=delta[:,:,:3]
            comparisons[path]=dict(
                changed_pixel_fraction=float(np.any(delta!=0,axis=2).mean()),
                alpha_changed_pixels=int(np.count_nonzero(delta[:,:,3])),
                rgb_mean_absolute=float(rgb.mean()),
                rgb_p99_absolute=float(np.percentile(rgb,99)),
                rgb_max_absolute=int(rgb.max()),
            )
        report['pixel_comparison']=comparisons
        picks=[path for path in sorted(comparisons) if '/4381/2917.png' in path or '/4385/2917.png' in path]
        pairs=out/'image-pairs';pairs.mkdir(exist_ok=True)
        for index,path in enumerate(picks):
            for label,body in [('python',reference_bodies[path]),('rust',native_bodies[path])]:
                (pairs/f'{index}-{label}.png').write_bytes(body)
        write_json(out/'report.json',report)
    report['passed']=all(r['failures']==0 for r in report['runs']) and not mismatches
    write_json(out/'report.json',report)
    if not args.allow_mismatch:assert report['passed'],'HTTP or image failures retained; this is not a successful viewport qualification'


if __name__=='__main__':main()
