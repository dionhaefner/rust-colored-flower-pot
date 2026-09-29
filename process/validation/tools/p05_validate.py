"""Offline P05 metadata, SQLite, CLI and optimization differential evidence."""
import argparse
import concurrent.futures
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import warnings

import numpy as np
import rasterio
from PIL import Image
from shapely.geometry import shape
import terracotta as tc
from terracotta.raster import compute_metadata
from terracotta.cog import validate
from terracotta.scripts.optimize_rasters import _optimize_single_raster
from rasterio.enums import Resampling
from common import ROOT, COMMIT, write_json, sha256

warnings.filterwarnings('ignore')
BINARY=ROOT/'target/debug/terracotta'
OUT=ROOT/'process/validation/artifacts/p05'
REPORT={'reference_commit':COMMIT,'checks':[]}

def check(name,condition,details=None):
    REPORT['checks'].append(dict(name=name,passed=bool(condition),details=details))
    write_json(OUT/'report.json',REPORT)
    if not condition: raise AssertionError((name,details))

def command(args,success=True):
    p=subprocess.run([str(BINARY),*map(str,args)],cwd=ROOT,text=True,capture_output=True)
    if success and p.returncode: raise RuntimeError((args,p.stdout,p.stderr))
    return p

def operation(request,success=True):
    import tempfile
    with tempfile.NamedTemporaryFile('w',suffix='.json',dir=OUT) as f:
        json.dump(request,f);f.flush();p=command(['data-operation',f.name],success)
    return json.loads(p.stdout) if p.returncode==0 else p

def normalized(m):
    return {k:np.asarray(v).tolist() if k in ['range','bounds','percentiles'] else v for k,v in m.items()}

def metadata_case(name,path,options):
    oracle=compute_metadata(str(path),**options)
    native=operation(dict(operation='compute_metadata',path=str(path),options=options))
    write_json(OUT/'metadata'/f'{name}-python.json',normalized(oracle));write_json(OUT/'metadata'/f'{name}-rust.json',native)
    metrics={}
    for field in ['bounds','range','valid_percentage','mean','stdev','percentiles']:
        a=np.asarray(oracle[field]);b=np.asarray(native[field]);diff=float(np.max(np.abs(a-b)));metrics[field]=diff
        if field=='percentiles':
            # Database float32 encoding is the native metadata boundary.
            passed=np.array_equal(a.astype('float32'),b.astype('float32'))
        elif field in ['mean','stdev']:
            passed=np.allclose(a,b,rtol=0,atol=4*float(np.spacing(np.float32(abs(a))))) if 'dolomites' in name and not options.get('use_chunks') else np.allclose(a,b,rtol=2e-12,atol=1e-10)
        else:passed=np.allclose(a,b,rtol=0,atol=1e-12)
        check(f'{name}-{field}',passed,diff)
    a=shape(oracle['convex_hull']);b=shape(native['convex_hull']);mismatch=a.symmetric_difference(b).area/a.union(b).area
    check(name+'-geometry',mismatch<(1e-9 if name=='rotated-chunk' else 1e-10),mismatch)
    check(name+'-extra',native['metadata']==oracle['metadata'])
    return oracle,native

def metadata_tests():
    for name in ['lagoon-red','lagoon-mask','dolomites','landcover']:
        path=ROOT/'.cache/corpus/subsets'/f'{name}.tif'
        for mode,options in [('full',{}),('sample',{'max_shape':[173,291]}),('chunk',{'use_chunks':True})]:
            metadata_case(name+'-'+mode,path,dict(options,extra_metadata={'source':name,'nested':[1,True,None]}))
    path=ROOT/'.cache/corpus/subsets/lagoon-red.tif'
    for mode,options in [('auto-chunk',{'large_raster_threshold':0}),('forced-full',{'large_raster_threshold':0,'use_chunks':False}),('sample-overrides-threshold',{'large_raster_threshold':0,'max_shape':[117,131]})]:
        metadata_case(mode,path,options)
    for extra in [None,False,0,"",[],{},["truthy"],"text"]:
        native=operation(dict(operation='compute_metadata',path=str(path),options={'max_shape':[10,10],'extra_metadata':extra}))
        check('extra-'+repr(extra),native['metadata']==(extra or {}))
    base=ROOT/'.cache/corpus/subsets/dolomites.tif'
    for label,value in [('constant',7),('negative',-7),('invalid',np.nan)]:
        path=OUT/f'{label}.tif'
        with rasterio.open(base) as src:
            profile=src.profile.copy();profile.update(width=32,height=32,nodata=np.nan)
            with rasterio.open(path,'w',**profile) as dst:dst.write(np.full((1,32,32),value,dtype='float32'))
        for chunk in [False,True]:
            if label=='invalid':
                p=operation(dict(operation='compute_metadata',path=str(path),options={'use_chunks':chunk}),False)
                check(f'invalid-{chunk}',p.returncode!=0 and 'valid data' in p.stderr)
            else:metadata_case(f'{label}-{chunk}',path,{'use_chunks':chunk})
    check('mutually-exclusive',operation(dict(operation='compute_metadata',path=str(base),options={'max_shape':[32,32],'use_chunks':True}),False).returncode!=0)

def database_tests():
    db=OUT/'roundtrip.sqlite';db.unlink(missing_ok=True)
    operation(dict(operation='create',path=str(db),keys=[{'key':'scene','description':'Scene'},{'key':'band','description':'Band'}]))
    raster=ROOT/'.cache/corpus/subsets/lagoon-red.tif';keys=['venice','red']
    operation(dict(operation='insert',path=str(db),keys=keys,raster=str(raster),options={'extra_metadata':{'hello':'world'}}))
    driver=tc.get_driver(str(db));m=driver.get_metadata(keys)
    check('rust-to-python',m['metadata']=={'hello':'world'} and list(driver.get_keys())==['scene','band'])
    driver.insert(['python','red'],str(raster),metadata=m)
    check('python-to-rust',operation(dict(operation='metadata',path=str(db),keys=['python','red']))['metadata']==m['metadata'])
    operation(dict(operation='insert',path=str(db),keys=keys,raster='replacement.tif',skip_metadata=True))
    check('skip-overwrite-retains-metadata',driver.get_metadata(keys)==m and driver.get_datasets()[tuple(keys)]=='replacement.tif')
    p=operation(dict(operation='batch',path=str(db),operations=[dict(operation='delete',keys=keys),dict(operation='delete',keys=['missing','x'])]),False)
    check('transaction-rollback',p.returncode!=0 and tuple(keys) in driver.get_datasets())
    operation(dict(operation='delete',path=str(db),keys=keys))
    check('delete-both-tables',tuple(keys) not in driver.get_datasets() and sqlite3.connect(db).execute("select count(*) from metadata where scene='venice'").fetchone()[0]==0)
    operation(dict(operation='insert',path=str(db),keys=['lazy','red'],raster=str(raster),skip_metadata=True))
    check('readonly-lazy-error',operation(dict(operation='metadata',path=str(db),keys=['lazy','red'],read_only=True),False).returncode!=0)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results=list(executor.map(lambda _:operation(dict(operation='metadata',path=str(db),keys=['lazy','red'])),range(8)))
    check('concurrent-lazy',all(v==results[0] for v in results) and sqlite3.connect(db).execute("select count(*) from metadata where scene='lazy'").fetchone()[0]==1)
    expected=compute_metadata(str(raster),max_shape=[1024,1024]);check('lazy-sample-percentiles',np.array_equal(results[0]['percentiles'],np.asarray(expected['percentiles'],dtype='float32')))
    driver.delete(['lazy','red']);check('python-delete-rust-row',operation(dict(operation='metadata',path=str(db),keys=['lazy','red'])) is None)

def optimization_tests():
    for name in ['lagoon-red','lagoon-mask','dolomites','landcover']:
        source=ROOT/'.cache/corpus/subsets'/f'{name}.tif'
        for mode in ['memory','disk','reproject']:
            folder=OUT/'optimized'/name/mode;folder.mkdir(parents=True,exist_ok=True)
            oracle_folder=folder/'python';oracle_folder.mkdir(exist_ok=True)
            options=dict(reproject=mode=='reproject',in_memory=mode=='memory',compression='deflate',resampling_method='average')
            command(['optimize-rasters',source,'-o',folder,'--overwrite','-q','--compression','deflate','--in-memory' if mode=='memory' else '--no-in-memory',*(['--reproject'] if mode=='reproject' else [])])
            _optimize_single_raster(source,oracle_folder,options['reproject'],Resampling.average,options['in_memory'],'DEFLATE',True,'')
            native=folder/source.name;oracle=oracle_folder/source.name
            check(f'{name}-{mode}-cog',validate(str(native)))
            with rasterio.open(native) as a,rasterio.open(oracle) as b:
                check(f'{name}-{mode}-grid',a.shape==b.shape and a.transform==b.transform and a.crs==b.crs and a.count==1)
                check(f'{name}-{mode}-values',np.array_equal(a.read(),b.read(),equal_nan=True))
                check(f'{name}-{mode}-mask',np.array_equal(a.dataset_mask(),b.dataset_mask()))
                check(f'{name}-{mode}-overviews',a.overviews(1)==b.overviews(1))
                for factor in a.overviews(1):
                    check(f'{name}-{mode}-overview-{factor}',np.array_equal(a.read(out_shape=(1,a.height//factor,a.width//factor)),b.read(out_shape=(1,b.height//factor,b.width//factor)),equal_nan=True))
            # Native preview both files with the same Python metadata (separate DB paths).
            images=[]
            from terracotta.server import create_app
            for label,file in [('python',oracle),('rust',native)]:
                db=folder/f'{label}.sqlite';db.unlink(missing_ok=True);d=tc.get_driver(str(db));d.create(['name']);d.insert(['image'],str(file))
                event=folder/f'{label}-event.json';write_json(event,{'httpMethod':'GET','path':'/singleband/image/preview.png','queryStringParameters':{'stretch_range':'[0,100]' if name=='landcover' else '[0,3000]'}})
                import base64
                response=json.loads(command(['lambda-event','-d',db,'--event',event]).stdout)
                check(f'{name}-{mode}-{label}-render',response['statusCode']==200)
                image=Image.open(io.BytesIO(base64.b64decode(response['body']))).convert('RGBA');images.append(np.array(image));image.save(folder/f'{label}.png')
            check(f'{name}-{mode}-visible',np.array_equal(*images),int(np.max(np.abs(images[0].astype(int)-images[1].astype(int)))))
    source=ROOT/'.cache/corpus/subsets/lagoon-red.tif'
    for codec in ['auto','zstd','lzw','none']:
        folder=OUT/'codecs'/codec;folder.mkdir(parents=True,exist_ok=True)
        command(['optimize-rasters',source,'-o',folder,'--overwrite','--compression',codec,'-q'])
        check('codec-'+codec,validate(str(folder/source.name)))
    folder=OUT/'codecs/auto';before=sha256(folder/source.name)
    check('optimize-existing-error',command(['optimize-rasters',source,'-o',folder],False).returncode!=0)
    command(['optimize-rasters',source,'-o',folder,'--skip-existing','-q']);check('optimize-skip',sha256(folder/source.name)==before)
    check('optimize-flags-conflict',command(['optimize-rasters',source,'-o',folder,'--overwrite','--skip-existing'],False).returncode!=0)

def cli_tests():
    from terracotta.scripts.click_types import RasterPattern
    import runpy
    tests=runpy.run_path(str(ROOT/'.cache/reference/tests/scripts/test_ingest.py'))
    tests['TEST_CASES'] += ({'filenames':['foofoo.tif'],'input_pattern':'{name}{name}.tif','expected_keys':['name'],'expected_datasets':[('foo',)]},)
    for index,case in enumerate(tests['TEST_CASES']+tests['INVALID_TEST_CASES']):
        directory=OUT/'patterns'/str(index);directory.mkdir(parents=True,exist_ok=True)
        for name in case['filenames']:
            file=directory/name;file.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/'.cache/corpus/subsets/lagoon-red.tif',file)
        db=directory/'database.sqlite';db.unlink(missing_ok=True)
        p=command(['ingest',directory/case['input_pattern'],'-o',db,'--skip-metadata','-q'],False)
        if 'expected_keys' not in case:check(f'pattern-invalid-{index}',p.returncode!=0 and case['error_contains'] in p.stderr.lower());continue
        d=tc.get_driver(str(db));check(f'pattern-{index}',p.returncode==0 and list(d.get_keys())==case['expected_keys'] and sorted(d.get_datasets())==sorted(case['expected_datasets']) and p.stdout=='')
    directory=OUT/'patterns/1';db=directory/'rgb.sqlite';db.unlink(missing_ok=True)
    command(['ingest',directory/'{sensor}_{date}_B04.tif','-o',db,'--rgb-key','sensor','-q'])
    d=tc.get_driver(str(db));check('rgb-key',list(d.get_keys())==['date','sensor'])
    check('ingest-computes',sqlite3.connect(db).execute('select count(*) from metadata').fetchone()[0]==1)
    before=sha256(db);command(['ingest',directory/'{sensor}_{date}_B04.tif','-o',db,'--rgb-key','sensor','--skip-existing','-q']);check('ingest-skip',sha256(db)==before)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--section',choices=['metadata','database','optimization','cli','all'],default='all');parser.add_argument('--output',type=Path,default=OUT);parser.add_argument('--binary',type=Path,default=BINARY);args=parser.parse_args();OUT=args.output.resolve();BINARY=args.binary.resolve();OUT.mkdir(parents=True,exist_ok=True)
    for name,fn in [('metadata',metadata_tests),('database',database_tests),('optimization',optimization_tests),('cli',cli_tests)]:
        if args.section in [name,'all']:print(name,flush=True);fn()
    REPORT['passed']=len(REPORT['checks']);write_json(OUT/'report.json',REPORT);print('PASS',REPORT['passed'])
