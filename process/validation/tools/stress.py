"""Explicit full-parent smoke with an RSS watchdog, native reads and I/O counters."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from common import ROOT, CACHE, read_json, write_json, sha256


def worker(out):
    import resource
    import numpy as np
    import rasterio
    import mercantile
    from terracotta import raster, image
    import psutil
    parent=CACHE/'worldcover.tif'
    assert sha256(parent)==read_json(ROOT/'process/validation/reference/assets.json')['assets'][-1]['sha256']
    process=psutil.Process(); results=[]
    with rasterio.open(parent) as src:
        source=dict(width=src.width,height=src.height,dtype=src.dtypes[0],overviews=src.overviews(1))
    options=dict(preserve_values=True,rio_env_options={'GDAL_CACHEMAX':32*1024**2})
    tile=mercantile.tile(12.33,45.44,15)
    for name,bounds in [('preview',None),('detail',tuple(mercantile.xy_bounds(tile)))]:
        before=process.io_counters(); start=time.perf_counter()
        arr=raster.get_raster_tile(str(parent),tile_bounds=bounds,tile_size=(256,256),**options)
        elapsed=time.perf_counter()-start; after=process.io_counters()
        assert arr.shape==(256,256) and arr.count()>0
        np.savez_compressed(out/(name+'.npz'),values=arr.data,mask=np.ma.getmaskarray(arr))
        from capture import PALETTE
        labels=list(map(int,PALETTE)); colors=[tuple(bytes.fromhex(c))+(255,) for c in PALETTE.values()]
        (out/(name+'.png')).write_bytes(image.array_to_png(image.label(arr,labels),colormap=colors).getvalue())
        results.append(dict(name=name,seconds=elapsed,masked=int(np.ma.getmaskarray(arr).sum()),
                            classes=np.unique(arr.compressed()).tolist(),bounds=bounds,
                            io_delta={k:getattr(after,k)-getattr(before,k) for k in before._fields}))
    return dict(source=source,results=results,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                detail_xyz=list(tile),file_bytes=parent.stat().st_size,
                decoded_value_bytes=36000*36000,decoded_value_plus_mask_bytes=36000*36000*2,
                float32_work_array_bytes=36000*36000*4,
                io_notes='Linux process counters: read_chars includes cached read syscalls; read_bytes is actual storage I/O. Checksum warms OS cache before measurements. No remote traffic.',
                overview_notes='Native full source has overviews. Broad preview permits automatic overview selection; z15 detail is finer than source 10m sampling and touches base-resolution data. No downsampled copy.')


def run(out,budget):
    import psutil
    if out.exists(): raise FileExistsError('choose a fresh stress output directory')
    out.mkdir(parents=True)
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    cmd=[sys.executable,__file__,'--worker','--out',str(out)]
    with (out/'worker.log').open('w') as log:
        proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=env)
        ps=psutil.Process(proc.pid); peak=0; exceeded=False
        while proc.poll() is None:
            try: peak=max(peak,ps.memory_info().rss)
            except psutil.NoSuchProcess: break
            if peak>budget:
                proc.kill(); exceeded=True; break
            time.sleep(.01)
        rc=proc.wait()
    result=dict(command=cmd,exit_code=rc,budget_bytes=budget,sampled_peak_rss_bytes=peak,budget_exceeded=exceeded)
    if rc==0:
        result.update(read_json(out/'worker.json'))
        result['passed']=result['peak_rss_bytes']<=budget and not exceeded
    else: result['passed']=False
    write_json(out/'stress.json',result)
    print(json.dumps(result,indent=2))
    if not result['passed']: raise SystemExit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--out',type=Path,required=True); p.add_argument('--budget-mib',type=int,default=512); p.add_argument('--worker',action='store_true')
    a=p.parse_args(); out=a.out.resolve()
    if a.worker: write_json(out/'worker.json',worker(out))
    else: run(out,a.budget_mib*1024**2)
