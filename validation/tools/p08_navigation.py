"""Measure newly visible map tiles: one and concurrent browsing sessions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading
import time
from urllib.parse import urlencode

import numpy as np
from PIL import Image
import psutil
import rasterio
import terracotta as tc
from common import ROOT, COMMIT, sha256, write_json
from p08_runtime import Service, fetch, image_hash


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
    p.add_argument('--repetitions',type=int,default=3)
    p.add_argument('--users',type=int,nargs='+',default=[1,4,8])
    args=p.parse_args();out=ROOT/args.out;out.mkdir(parents=True,exist_ok=False)
    assets=json.loads((ROOT/'validation/reference/assets.json').read_text())['assets']
    database=out/'catalog.sqlite';driver=tc.get_driver(database);driver.create(['scene','band'])
    sources=[]
    for band in ['red','green','blue']:
        asset=next(a for a in assets if a['id']=='s2-'+band);path=ROOT/'.cache/corpus'/('s2-'+band+'.tif')
        assert sha256(path)==asset['sha256']
        driver.insert(('scene',band),str(path),metadata=driver.compute_metadata(str(path),max_shape=[512,512]))
        with rasterio.open(path) as src:sources.append(dict(path=str(path.relative_to(ROOT)),sha256=asset['sha256'],shape=list(src.shape),overviews=src.overviews(1)))
    cpus=psutil.Process().cpu_affinity()[:3];assert len(cpus)==3
    report=dict(reference_commit=COMMIT,native_sha256=sha256(ROOT/'validation/artifacts/p08-final/bin/terracotta'),sources=sources,cpu_affinity=cpus,
        conditions=dict(viewport=[1024,768],tile_size=256,zoom=13,requests_in_flight_per_user=6,pan_pause_seconds=.1,views_per_session=7,
            total_gdal_cache_mib=96,total_application_cache_mib=64,rust_raster_workers=3,python_gunicorn_workers=3,
            rust_queue_capacity=256,access='local original COGs, warm OS disk cache; fresh service for every trial',
            scope='Server-side newly visible tile completion, not browser animation/paint or WAN latency. Sessions keep downloaded tiles. Spatially offset concurrent tracks have natural overlap. No retries or cancelled requests. Python cache compressed per process; native uncompressed shared. RSS sums shared Python pages.'),runs=[])
    golden={};coverage={};golden_lock=threading.Lock()
    for trial in range(args.repetitions):
        for users in args.users:
            for mode in ['singleband','rgb']:
                routes=tracks(users,mode)
                for engine in (['python','rust'] if trial%2==0 else ['rust','python']):
                    label=f'{trial}-{users}-{mode}-{engine}'
                    extra=dict(GDAL_CACHEMAX='96' if engine=='rust' else '32',TC_RASTER_QUEUE_CAPACITY='256')
                    with Service(database,out/label,engine,64*1024**2,cpus,extra) as service:
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
                                                    with Image.open(io.BytesIO(body)) as im:
                                                        a=np.asarray(im.convert('RGBA'));coverage[path]=float(np.count_nonzero(a[:,:,3]))/(a.shape[0]*a.shape[1])
                                                assert coverage[path]>=.95,(path,'insufficient real-image coverage',coverage[path])
                                                assert digest==golden[path],(engine,path,'decoded pixels differ')
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
                    report['runs'].append(row);report['coverage']=coverage;report['decoded_image_hashes']=golden
                    write_json(out/'report.json',report)
                    print(label,'first',round(row['first_view']['p50_ms']),'pan',round(row['pan']['p50_ms']),'p95',round(row['pan']['p95_ms']),'failures',row['failures'],flush=True)
    report['passed']=all(r['failures']==0 for r in report['runs'])
    write_json(out/'report.json',report)
    assert report['passed'],'HTTP failures retained; this is not a successful viewport qualification'


if __name__=='__main__':main()
