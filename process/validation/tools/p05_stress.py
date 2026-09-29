"""Full real WorldCover chunk statistics and disk optimization, measured offline."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
import numpy as np
import psutil
import rasterio
from common import ROOT,write_json,sha256,read_json

OUT=Path(os.environ.get('TC_P05_STRESS_OUT',ROOT/'process/validation/artifacts/p05-stress'))
SOURCE=ROOT/'.cache/corpus/worldcover.tif'

def monitor(args,name):
    env=dict(os.environ,GDAL_CACHEMAX='64')
    started=time.monotonic();peak=0
    with (OUT/(name+'.stdout')).open('w') as stdout,(OUT/(name+'.stderr')).open('w') as stderr:
        p=subprocess.Popen(args,cwd=ROOT,env=env,stdout=stdout,stderr=stderr)
        while p.poll() is None:
            try:peak=max(peak,psutil.Process(p.pid).memory_info().rss)
            except psutil.NoSuchProcess:pass
            time.sleep(.05)
    result=dict(command=list(map(str,args)),exit_code=p.returncode,peak_rss=peak,seconds=time.monotonic()-started)
    write_json(OUT/(name+'-process.json'),result)
    if p.returncode:raise RuntimeError(result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--worker',choices=['reference-stats','reference-optimize']);p.add_argument('--binary',type=Path,default=ROOT/'target/release/terracotta');a=p.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    if a.worker:
        if a.worker=='reference-stats':
            from terracotta.raster import compute_metadata
            m=compute_metadata(str(SOURCE),use_chunks=True)
            m['percentiles']=m['percentiles'].tolist();write_json(OUT/'python.json',m)
        else:
            from terracotta.scripts.optimize_rasters import _optimize_single_raster
            from rasterio.enums import Resampling
            folder=OUT/'python';folder.mkdir(exist_ok=True)
            _optimize_single_raster(SOURCE,folder,False,Resampling.average,False,'DEFLATE',True,'')
        sys.exit(0)
    expected=read_json(ROOT/'process/validation/reference/assets.json')['assets'][-1]['sha256'];assert sha256(SOURCE)==expected
    request=OUT/'stats-request.json';write_json(request,dict(operation='compute_metadata',path=str(SOURCE),options={'use_chunks':True}))
    processes=[]
    processes.append(monitor([str(a.binary.resolve()),'data-operation',str(request)],'rust-stats'))
    processes.append(monitor([sys.executable,__file__,'--worker','reference-stats'],'python-stats'))
    rust=json.loads((OUT/'rust-stats.stdout').read_text());python=read_json(OUT/'python.json');metrics={}
    for key in ['range','mean','stdev','valid_percentage','percentiles','bounds']:
        x=np.asarray(python[key]);y=np.asarray(rust[key]);metrics[key]=float(np.max(np.abs(x-y)))
        if key=='percentiles':assert np.array_equal(x.astype('float32'),y.astype('float32')),(key,metrics[key])
        else:assert np.allclose(x,y,rtol=2e-12,atol=1e-10),(key,metrics[key])
    from shapely.geometry import shape
    x=shape(python['convex_hull']);y=shape(rust['convex_hull']);metrics['hull_relative_area']=x.symmetric_difference(y).area/x.union(y).area;assert metrics['hull_relative_area']<1e-10
    folder=OUT/'rust';folder.mkdir(exist_ok=True)
    processes.append(monitor([str(a.binary.resolve()),'optimize-rasters',str(SOURCE),'-o',str(folder),'--overwrite','--no-in-memory','--compression','deflate','-q'],'rust-optimize'))
    processes.append(monitor([sys.executable,__file__,'--worker','reference-optimize'],'python-optimize'))
    from terracotta.cog import validate
    assert validate(str(folder/SOURCE.name));assert validate(str(OUT/'python'/SOURCE.name))
    with rasterio.open(folder/SOURCE.name) as x,rasterio.open(OUT/'python'/SOURCE.name) as y:
        assert x.shape==y.shape and x.transform==y.transform and x.overviews(1)==y.overviews(1)
        for _,w in x.block_windows(1):
            assert np.array_equal(x.read(1,window=w),y.read(1,window=w))
            assert np.array_equal(x.dataset_mask(window=w),y.dataset_mask(window=w))
        for n in x.overviews(1):
            # Read one block at a time even for the largest overview.
            with rasterio.open(folder/SOURCE.name,OVERVIEW_LEVEL=x.overviews(1).index(n)) as xx,rasterio.open(OUT/'python'/SOURCE.name,OVERVIEW_LEVEL=y.overviews(1).index(n)) as yy:
                for _,w in xx.block_windows(1):assert np.array_equal(xx.read(1,window=w),yy.read(1,window=w))
    assert all(v['peak_rss']<512*1024**2 for v in processes),processes
    write_json(OUT/'report.json',dict(passed=True,source_sha256=expected,shape=[36000,36000],metrics=metrics,processes=processes,budget_bytes=512*1024**2))
    print('PASS full WorldCover statistics and disk optimization')
