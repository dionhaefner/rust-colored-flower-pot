"""Offline polar/dateline DEM differential, pinned by the acquisition manifest."""
import io
import json
import os
import subprocess
import sys
from urllib.parse import urlencode

import mercantile
import numpy as np
from PIL import Image, ImageDraw
import rasterio
from rasterio.windows import Window
import terracotta as tc
from terracotta.server import create_app
from common import COMMIT, ROOT, sha256, write_json
from p08_runtime import Service, fetch


def main():
    out = ROOT/os.environ.get('TC_GEOGRAPHY_OUT','validation/artifacts/p08-geography');out.mkdir(parents=True,exist_ok=False)
    images = out/'images'; images.mkdir()
    assets = json.loads((ROOT/'validation/evidence/p08/geography-assets.json').read_text())
    db=out/'catalog.sqlite';driver=tc.get_driver(db);driver.create(['scene','band'])
    cases=[];provenance=[]
    for asset in assets:
        source=ROOT/asset['path'];assert sha256(source)==asset['sha256']
        with rasterio.open(source) as src:
            # Native-resolution crop around the highest terrain, retaining recognizable land.
            values=src.read(1); row,col=np.unravel_index(np.argmax(values),values.shape)
            width=min(768,src.width);height=min(768,src.height)
            window=Window(min(max(int(col)-width//2,0),src.width-width),min(max(int(row)-height//2,0),src.height-height),width,height)
            profile=src.profile.copy();profile.update(width=width,height=height,transform=src.window_transform(window))
            path=out/(asset['tile']+'.tif')
            with rasterio.open(path,'w',**profile) as dst:dst.write(src.read(window=window))
            bounds=rasterio.windows.bounds(window,src.transform)
            provenance.append(dict(source=asset,window=list(window.flatten()),profile={k:str(v) for k,v in profile.items()},sha256=sha256(path),bounds=bounds))
        scene=asset['tile'];driver.insert((scene,'elevation'),str(path))
        options=urlencode(dict(colormap='terrain',stretch_range='[0,1500]'))
        cases.append((scene+'-preview',f'/singleband/{scene}/elevation/preview.png?{options}'))
        cases.append((scene+'-nonsquare',f'/singleband/{scene}/elevation/preview.png?{options}&tile_size=[128,384]'))
        lon=(bounds[0]+bounds[2])/2;lat=(bounds[1]+bounds[3])/2
        for zoom in [0,5,10,14]:
            tile=mercantile.tile(lon,lat,zoom)
            for dx in ([0,1] if zoom==10 else [0]):
                cases.append((f'{scene}-z{zoom}-dx{dx}',f'/singleband/{scene}/elevation/{zoom}/{(tile.x+dx)%(2**zoom)}/{tile.y}.png?{options}'))
    # Whole publisher assets at both sides of the meridian retain the real boundary.
    for asset in assets[1:]:
        scene=asset['tile']+'-full';driver.insert((scene,'elevation'),str(ROOT/asset['path']))
        for x in [0,1023]:
            y=mercantile.tile(179.999,51.5,10).y
            cases.append((f'{scene}-edge-{x}',f'/singleband/{scene}/elevation/10/{x}/{y}.png?stretch_range=[0,1500]&colormap=terrain'))
    # Two truly adjacent publisher tiles across the meridian. Unwrap the western
    # tile's longitude by +360 and concatenate native columns; never relocate
    # unrelated pixels or fill a synthetic scene to claim geographic coverage.
    mosaic=out/'dateline-mosaic.tif'
    with rasterio.open(ROOT/assets[1]['path']) as east,rasterio.open(ROOT/assets[2]['path']) as west:
        assert east.shape==west.shape and east.transform.a==west.transform.a
        assert abs(east.bounds.right-(west.bounds.left+360))<1e-10
        profile=east.profile.copy();profile.update(width=east.width+west.width)
        with rasterio.open(mosaic,'w',**profile) as dst:
            dst.write(east.read(1),1,window=Window(0,0,east.width,east.height))
            dst.write(west.read(1),1,window=Window(east.width,0,west.width,west.height))
        provenance.append(dict(derivative='dateline-mosaic',sources=[assets[1]['sha256'],assets[2]['sha256']],
            recipe='Concatenate all native columns east then west; western longitude unwrapped by +360; no value/mask/resolution resampling. Nodata remains zero.',sha256=sha256(mosaic)))
    driver.insert(('dateline-mosaic','elevation'),str(mosaic))
    for suffix in ['preview.png',f'10/0/{mercantile.tile(-179.9,51.5,10).y}.png',f'10/1023/{mercantile.tile(179.9,51.5,10).y}.png']:
        cases.append(('dateline-mosaic-'+suffix.replace('/','-'),'/singleband/dateline-mosaic/elevation/'+suffix+'?stretch_range=[0,1500]&colormap=terrain'))
    cases.append(('dateline-mosaic-rgb','/rgb/dateline-mosaic/preview.png?'+urlencode(dict(r='elevation',g='elevation',b='elevation',r_range='[0,1500]',g_range='[0,1500]',b_range='[0,1500]'))))
    cases.append(('dateline-mosaic-compute','/compute/dateline-mosaic/preview.png?'+urlencode(dict(expression='v1',v1='elevation',stretch_range='[0,1500]',colormap='terrain'))))
    tc.update_settings(DRIVER_PATH=str(db),USE_MULTIPROCESSING=False,RASTER_CACHE_SIZE=0)
    client=create_app().test_client();results=[]
    with Service(db,out/'server') as service:
        for name,path in cases:
            ref=client.get(path);status,body=fetch(service.base,path)
            (images/(name+'-python.png')).write_bytes(ref.data);(images/(name+'-rust.png')).write_bytes(body)
            a=np.asarray(Image.open(io.BytesIO(ref.data)).convert('RGBA'));b=np.asarray(Image.open(io.BytesIO(body)).convert('RGBA'))
            passed=ref.status_code==status==200 and np.array_equal(a,b)
            results.append(dict(name=name,path=path,passed=passed,shape=list(a.shape),mask_pixels=int(np.count_nonzero(a[:,:,3])),different_channels=int(np.count_nonzero(a!=b))))
            print(name,passed,results[-1]['different_channels'],flush=True)
    sheet=Image.new('RGB',(768,3*280),'white');draw=ImageDraw.Draw(sheet)
    for row,asset in enumerate(assets):
        name=asset['tile']+'-preview'
        for col,engine in enumerate(['python','rust']):
            with Image.open(images/(name+'-'+engine+'.png')) as im:
                rgba=im.convert('RGBA');sheet.paste(rgba,(col*384,row*280),rgba)
        draw.text((5,row*280+258),name+' Python | Rust',fill='black')
    sheet.save(out/'side-by-side.png')
    write_json(out/'report.json',dict(reference_commit=COMMIT,passed=all(r['passed'] for r in results),results=results,provenance=provenance))
    assert all(r['passed'] for r in results)


if __name__=='__main__':main()
