"""P04 real-raster derivatives, native read settings, custom palettes and CORS."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import urllib.request
import urllib.error

import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import ColorInterp
import terracotta as tc
from terracotta.server import create_app
from terracotta import cmaps
from terracotta.cmaps import get_cmaps
from common import ROOT,COMMIT,write_json
from p01_validate import wait_ready
from p02_validate import clone_row, SETTINGS


def run(out,port):
    out.mkdir(parents=True,exist_ok=False);(out/'images').mkdir();custom=out/'palettes';custom.mkdir()
    colors=np.tile(np.arange(255,dtype='uint8')[:,None],(1,4));colors[:,3]=255;colors[32:64,3]=128
    np.save(custom/'custom_rgba.npy',np.asfortranarray(colors))
    np.save(custom/'viridis_rgba.npy',colors[::-1])
    get_cmaps.CMAP_FILES['viridis']=str(custom/'viridis_rgba.npy')
    get_cmaps.AVAILABLE_CMAPS.append('custom');get_cmaps.CMAP_FILES['custom']=str(custom/'custom_rgba.npy')
    # schemas import the same list; app imports them after this addition.
    database=out/'database.sqlite';shutil.copy2(ROOT/'process/validation/artifacts/capture-reproduction/reference.sqlite',database)
    src=ROOT/'.cache/corpus/subsets/lagoon-red.tif'
    for mode in ['alpha','zero']:
        path=out/f'{mode}.tif'
        with rasterio.open(src) as source:
            data=source.read(1);mask=source.read_masks(1);profile=source.profile.copy();profile.update(nodata=None,count=2 if mode=='alpha' else 1)
            if mode=='zero':data[200:300,200:300]=0
            with rasterio.open(path,'w',**profile) as dest:
                dest.write(data,1)
                if mode=='alpha':
                    dest.write(mask.astype(data.dtype),2);dest.colorinterp=(ColorInterp.gray,ColorInterp.alpha)
        with sqlite3.connect(database) as conn:
            clone_row(conn,'datasets',('lagoon','red'),(mode,'red'),str(path));clone_row(conn,'metadata',('lagoon','red'),(mode,'red'))
    settings=[]
    for resample in ['nearest','linear','cubic','average']:
        for reproject in ['nearest','linear','cubic','average']:
            settings.append(dict(RESAMPLING_METHOD=resample,REPROJECTION_METHOD=reproject))
    settings += [dict(ALLOWED_ORIGINS_TILES=v,ALLOWED_ORIGINS_METADATA=v) for v in [['*'],[],['example.org'],['https://.*example.org']]]
    settings += [dict(DEFAULT_TILE_SIZE=[96,128],MAX_POST_METADATA_KEYS=1)]
    results=[]
    for index,config in enumerate(settings):
        tc.update_settings(DRIVER_PATH=str(database),**dict(SETTINGS,ALLOWED_ORIGINS_TILES=[r'http[s]?://(localhost|127\.0\.0\.1):*'],ALLOWED_ORIGINS_METADATA=['*'],DEFAULT_TILE_SIZE=[256,256],MAX_POST_METADATA_KEYS=100,**{}))
        tc.update_settings(**config);app=create_app();app.logger.disabled=True;client=app.test_client()
        env=dict(os.environ,TC_EXTRA_CMAP_FOLDER=str(custom))
        env.update({'TC_'+k:json.dumps(v) if isinstance(v,list) else str(v) for k,v in config.items()})
        requests=[('/singleband/lagoon/red/preview.png',{}),('/singleband/dolomites/elevation/preview.png',{}),('/singleband/lagoon/classes/preview.png?colormap=explicit&explicit_color_map=%7B%2210%22%3A%5B0%2C100%2C0%5D%2C%2250%22%3A%5B255%2C0%2C0%5D%7D',{})]
        if index==0:
            requests += [('/singleband/alpha/red/preview.png',{}),('/singleband/zero/red/preview.png?stretch_range=[0,3000]',{}),('/singleband/lagoon/red/0/0/0.png',{}),('/singleband/lagoon/red/16/35008/23456.png?tile_size=[64,128]',{}),('/singleband/lagoon/red/preview.png?colormap=custom',{}),('/colormap?colormap=custom&stretch_range=[0,1]&num_values=5',{}),('/singleband/lagoon/red/preview.png?colormap=viridis',{}),('/singleband/lagoon/red/preview.png?colormap=custom&reload=1',{})]
        if 'ALLOWED_ORIGINS_TILES' in config:
            requests=[(p,h) for p in ['/keys','/singleband/lagoon/red/preview.png'] for h in [{},{'Origin':'example.org'},{'Origin':'https://foo.example.org'},{'Origin':'http://foreign.test'}]]
        with (out/f'server-{index}.log').open('w') as log:
            proc=subprocess.Popen([str(ROOT/'target/debug/terracotta'),'serve','-d',str(database),'--bind',f'127.0.0.1:{port}'],env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            try:
                wait_ready(f'http://127.0.0.1:{port}',proc)
                for j,(url,headers) in enumerate(requests):
                    if 'reload=1' in url:
                        np.save(custom/'custom_rgba.npy',colors[::-1].copy())
                    ref=client.get(url,headers=headers)
                    try:r=urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}'+url,headers=headers),timeout=120)
                    except urllib.error.HTTPError as e:r=e
                    body=r.read();errors=[]
                    if ref.status_code!=r.status:errors.append(f'status {r.status} != {ref.status_code}')
                    for h in ['Content-Type','Cache-Control','Access-Control-Allow-Origin','Vary']:
                        if ref.headers.get(h)!=r.headers.get(h):errors.append(f'{h} {r.headers.get(h)} != {ref.headers.get(h)}')
                    if ref.content_type=='image/png':
                        (out/'images'/f'{index}-{j}-python.png').write_bytes(ref.data);(out/'images'/f'{index}-{j}-rust.png').write_bytes(body)
                        try:
                            a=np.asarray(Image.open(io.BytesIO(ref.data)).convert('RGBA'));b=np.asarray(Image.open(io.BytesIO(body)).convert('RGBA'))
                            if a.shape!=b.shape:errors.append(f'shape {b.shape} != {a.shape}')
                            elif not np.array_equal(a,b):errors.append(f'{np.count_nonzero(a!=b)} channels differ; max {np.abs(a.astype(int)-b).max()}')
                        except Exception as e:errors.append(str(e))
                    elif ref.is_json and ref.json!=json.loads(body):errors.append('JSON differs')
                    results.append(dict(settings=config,url=url,headers=headers,passed=not errors,errors=errors))
                    if errors:print(config,url,errors,flush=True)
            finally:proc.terminate();proc.wait(timeout=10)
    # Invalid custom files must fail startup, not silently disappear.
    invalid=out/'invalid-palettes';invalid.mkdir()
    for name,array in [('shape',np.zeros((254,4),dtype='uint8')),('dtype',np.zeros((255,4),dtype='float32'))]:
        np.save(invalid/'invalid_rgba.npy',array)
        proc=subprocess.run([str(ROOT/'target/debug/terracotta'),'serve','-d',str(database),'--bind',f'127.0.0.1:{port}'],env=dict(os.environ,TC_EXTRA_CMAP_FOLDER=str(invalid)),capture_output=True,text=True,timeout=15)
        results.append(dict(case='invalid-custom-'+name,passed=proc.returncode==2 and 'invalid custom colormap' in proc.stderr,stderr=proc.stderr,returncode=proc.returncode))
    write_json(out/'report.json',dict(reference_commit=COMMIT,results=results,summary=dict(passed=sum(r['passed'] for r in results),failed=sum(not r['passed'] for r in results),total=len(results))))
    print('passed',sum(r['passed'] for r in results),'/',len(results))
    if any(not r['passed'] for r in results):raise SystemExit(1)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--port',type=int,default=5105);a=p.parse_args();run(a.out.resolve(),a.port)
