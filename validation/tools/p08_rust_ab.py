"""Alternate two native builds on the existing zoom-13 UTM pan workload."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

import psutil
import p08_runtime
from p08_runtime import Service, fetch, image_hash
from p08_navigation import tracks, distribution
from common import sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--users',type=int,nargs='+',default=[1,4,8])
    parser.add_argument('--modes',nargs='+',choices=['singleband','rgb'],default=['singleband','rgb'])
    parser.add_argument('--candidate-no-prefetch',action='store_true')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    cpus=psutil.Process().cpu_affinity()[:3]
    assert len(cpus)==3
    report=dict(binaries={'baseline':sha256(args.baseline),'candidate':sha256(args.candidate)},runs=[])
    gold={};lock=threading.Lock()
    for trial in range(args.repetitions):
        for users in args.users:
            for mode in args.modes:
                for name in (['baseline','candidate'] if trial%2==0 else ['candidate','baseline']):
                    p08_runtime.BINARY=getattr(args,name).resolve()
                    routes=tracks(users,mode)
                    extra={'GDAL_CACHEMAX':'16','TC_RUST_UTM_FAST':'1','TC_PREFETCH_TILE_RING':'1','TC_SOURCE_BLOCK_CACHE_BYTES':str(112*1024**2),'TC_RASTER_QUEUE_CAPACITY':'256'}
                    if name=='candidate' and args.candidate_no_prefetch:extra.pop('TC_PREFETCH_TILE_RING')
                    with Service(args.database,args.out/f'{trial}-{users}-{mode}-{name}',cache=32*1024**2,cpus=cpus,extra=extra) as service:
                        barrier=threading.Barrier(users)
                        def session(user):
                            result=[]
                            with ThreadPoolExecutor(6) as requests:
                                barrier.wait()
                                for view in routes[user]:
                                    def request(path):
                                        status,body=fetch(service.base,path)
                                        received=time.perf_counter()
                                        if status!=200:raise AssertionError((name,path,status))
                                        digest=image_hash(body)
                                        with lock:
                                            if name=='baseline':gold[path]=digest
                                            elif digest!=gold[path]:raise AssertionError((path,'decoded image changed'))
                                        return received
                                    start=time.perf_counter()
                                    arrival=list(requests.map(request,view['paths']))
                                    result.append(dict(step=view['step'],seconds=max(arrival)-start))
                                    time.sleep(.1)
                            return result
                        with ThreadPoolExecutor(users) as sessions:views=[v for group in sessions.map(session,range(users)) for v in group]
                        row=dict(trial=trial,users=users,mode=mode,binary=name,
                                 first=distribution([v['seconds'] for v in views if v['step']==0]),
                                 pan=distribution([v['seconds'] for v in views if v['step']>0]),
                                 resources=service.resources(),metrics=service.metrics())
                    report['runs'].append(row);write_json(args.out/'report.json',report)
                    print(trial,users,mode,name,'pan',round(row['pan']['p50_ms'],2),flush=True)
    report['passed']=True
    report['checked_urls']=len(gold)
    write_json(args.out/'report.json',report)


if __name__=='__main__':main()
