"""Additional mask, affine, resampling and failure contracts for P05."""
import json,os,sys
from pathlib import Path
import numpy as np,rasterio
from rasterio.enums import ColorInterp,Resampling
from rasterio.transform import Affine
from terracotta.raster import compute_metadata
from terracotta.scripts.optimize_rasters import _optimize_single_raster
from terracotta.cog import validate
import p05_validate as v
from common import ROOT,write_json
v.OUT=ROOT/'validation/artifacts/p05-edges';v.OUT.mkdir(parents=True,exist_ok=True);v.BINARY=ROOT/'target/release/terracotta'
source=ROOT/'.cache/corpus/subsets/lagoon-mask.tif'
# Real-raster alpha and multiband derivatives; isolate dataset mask priority.
with rasterio.open(source) as src:
    data=src.read(1,window=((0,512),(0,512)));mask=src.dataset_mask(window=((0,512),(0,512)));profile=src.profile.copy();profile.update(width=512,height=512)
for label in ['alpha','multinodata','nan','rotated']:
    path=v.OUT/(label+'.tif');p=profile.copy()
    if label=='alpha':p.update(count=2)
    if label=='multinodata':p.update(count=2,nodata=0)
    if label=='nan':p.update(dtype='float32',nodata=None)
    if label=='rotated':p.update(transform=p['transform']*Affine.rotation(15))
    with rasterio.open(path,'w',**p) as dst:
        values=data.astype(p['dtype']);values[200:205,200:205]=np.nan if label=='nan' else 0;dst.write(values,1)
        if label=='alpha':dst.write(mask.astype('uint16'),2);dst.colorinterp=(ColorInterp.gray,ColorInterp.alpha)
        elif label=='multinodata':dst.write(np.maximum(values,1),2)
        else:dst.write_mask(mask)
    for mode,options in [('full',{}),('sample',{'max_shape':[133,197]}),('chunk',{'use_chunks':True})]:v.metadata_case(label+'-'+mode,path,options)
    for reproject in [False,True]:
        folder=v.OUT/label/str(reproject);folder.mkdir(parents=True,exist_ok=True);reference=folder/'python';reference.mkdir(exist_ok=True)
        v.command(['optimize-rasters',path,'-o',folder,'--overwrite','-q',*(['--reproject']if reproject else [])])
        _optimize_single_raster(path,reference,reproject,Resampling.average,True,'ZSTD',True,'')
        with rasterio.open(folder/path.name) as a,rasterio.open(reference/path.name) as b:
            v.check(label+f'-optimize-{reproject}-values',np.array_equal(a.read(),b.read(),equal_nan=True))
            v.check(label+f'-optimize-{reproject}-mask',np.array_equal(a.dataset_mask(),b.dataset_mask()))
        v.check(label+f'-optimize-{reproject}-cog',validate(str(folder/path.name)))
source=ROOT/'.cache/corpus/subsets/lagoon-red.tif'
for method in ['nearest','bilinear','cubic']:
    folder=v.OUT/method;folder.mkdir(exist_ok=True);reference=folder/'python';reference.mkdir(exist_ok=True)
    v.command(['optimize-rasters',source,'-o',folder,'--overwrite','--reproject','--resampling-method',method,'--nproc','2','-q'])
    _optimize_single_raster(source,reference,True,getattr(Resampling,method),False,'ZSTD',True,'')
    with rasterio.open(folder/source.name) as a,rasterio.open(reference/source.name) as b:
        v.check(method+'-base',np.array_equal(a.read(),b.read(),equal_nan=True))
        v.check(method+'-mask',np.array_equal(a.dataset_mask(),b.dataset_mask()))
        for n in a.overviews(1):v.check(method+f'-overview-{n}',np.array_equal(a.read(out_shape=(1,a.height//n,a.width//n)),b.read(out_shape=(1,b.height//n,b.width//n)),equal_nan=True))
v.REPORT['passed']=len(v.REPORT['checks']);write_json(v.OUT/'report.json',v.REPORT);print('PASS',v.REPORT['passed'])
# Actual multi-file worker run, with independent outputs and quiet behavior.
folder=v.OUT/'parallel';folder.mkdir(exist_ok=True)
p=v.command(['optimize-rasters',ROOT/'.cache/corpus/subsets/lagoon-red.tif',ROOT/'.cache/corpus/subsets/landcover.tif','-o',folder,'--overwrite','--nproc','2','-q'])
v.check('parallel-quiet',p.stdout=='')
for name in ['lagoon-red','landcover']:
    with rasterio.open(folder/(name+'.tif')) as a,rasterio.open(ROOT/'.cache/corpus/subsets'/(name+'.tif')) as b:
        v.check('parallel-'+name,np.array_equal(a.read(),b.read()) and np.array_equal(a.dataset_mask(),b.dataset_mask()))
    v.check('parallel-cog-'+name,validate(str(folder/(name+'.tif'))))
v.REPORT['passed']=len(v.REPORT['checks']);write_json(v.OUT/'report.json',v.REPORT);print('PASS including parallel',v.REPORT['passed'])
