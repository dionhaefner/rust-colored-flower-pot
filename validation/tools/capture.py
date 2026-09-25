"""Capture the unchanged Python oracle offline. Refuses to overwrite a capture."""
import argparse
import html
import json
import os
from pathlib import Path
import sqlite3
import statistics
import time
from urllib.parse import urlencode
import numpy as np
from PIL import Image
import mercantile
import rasterio
from rasterio.warp import transform_bounds
import terracotta as tc
from terracotta.server import create_app
from common import ROOT, CACHE, COMMIT, read_json, write_json, sha256

PALETTE={'10':'006400','20':'ffbb22','30':'ffff4c','40':'f096ff','50':'fa0000','60':'b4b4b4','70':'f0f0f0','80':'0064c8','90':'0096a0','95':'00cf75','100':'fae6a0'}
SETTINGS=dict(DRIVER_PROVIDER='sqlite',RASTER_CACHE_SIZE=0,USE_MULTIPROCESSING=False,
              RESAMPLING_METHOD='average',REPROJECTION_METHOD='linear',PNG_COMPRESS_LEVEL=1)


def database_dump(path):
    result=[]
    with sqlite3.connect(path) as conn:
        for name,sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
            cols=[r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            rows=conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            result.append(dict(table=name,sql=sql,columns=cols,rows=[[
                {'blob_hex':v.hex()} if isinstance(v,bytes) else v for v in row] for row in rows]))
    return result


def capture(out):
    if out.exists(): raise FileExistsError(f'{out}: use a new output directory; reference is never refreshed implicitly')
    # Check inputs before creating output; no acquisition happens here.
    corpus=read_json(ROOT/'validation/evidence/p00/corpus.json')
    recipes=read_json(ROOT/'validation/reference/subsets.json')['subsets']
    for r in recipes:
        if sha256(CACHE/'subsets'/(r['id']+'.tif'))!=corpus['subsets'][r['id']]['sha256']:
            raise ValueError(f'changed input: {r["id"]}')
    os.chdir(ROOT)
    out.mkdir(parents=True); (out/'images').mkdir(); (out/'arrays').mkdir()
    db=out/'reference.sqlite'
    tc.update_settings(DRIVER_PATH=str(db),**SETTINGS)
    driver=tc.get_driver(str(db)); driver.create(['scene','band'],key_descriptions={'scene':'Real source subset','band':'Physical band or class'})
    for r in recipes:
        # Relative paths intentionally preserve relocatability; cwd is declared as repository root.
        path=str((CACHE/'subsets'/(r['id']+'.tif')).relative_to(ROOT))
        driver.insert(r['keys'],path,metadata=driver.compute_metadata(path,extra_metadata={'subset':r['id']}))
    client=create_app().test_client()
    contract={'format':1,'settings':SETTINGS,'requests':[],'storage':database_dump(db)}
    record={'reference_commit':COMMIT,'environment_sha256':sha256(ROOT/'validation/reference/environment.json'),
            'input_checksums':{r['id']:corpus['subsets'][r['id']]['sha256'] for r in recipes},
            'contract':contract,'images':[],'arrays':[]}
    views=[]; timings=[]
    def request(id,path,query=None,method='GET',body=None,expected=200,view=None):
        url=path+('?' + urlencode(query) if query else '')
        begin=time.perf_counter()
        response=client.open(url,method=method,json=body,headers={'Origin':'http://localhost:8080'})
        elapsed=time.perf_counter()-begin
        assert response.status_code==expected,(id,response.status_code,response.data[:300])
        headers={k.lower():v for k,v in response.headers if k.lower() in ('content-type','cache-control','vary') or k.lower().startswith('access-control-')}
        item=dict(id=id,method=method,url=url,body=body,status=response.status_code,headers=headers)
        if response.mimetype=='image/png':
            name=f'images/{id}.png'; (out/name).write_bytes(response.data); record['images'].append(name)
            with Image.open(out/name) as im: item['dimensions']=list(im.size)
            if view: views.append(dict(id=id,image=name,url=url,**view))
        elif response.is_json: item['json']=response.get_json()
        else: item['text']=response.get_data(as_text=True)
        contract['requests'].append(item); timings.append(dict(id=id,seconds=elapsed))
        return response
    for id,path,query in [('keys','/keys',None),('datasets','/datasets',None),('paging','/datasets',{'limit':2,'page':1,'order_by':'[scene,band]','ascending':'false'}),('filter','/datasets',{'scene':'lagoon','band':'[red,nir]'}),('metadata','/metadata/lagoon/red',None),('columns','/metadata/lagoon/red',{'columns':'["range","bounds"]'}),('colormap','/colormap',{'stretch_range':'[0,100]','colormap':'viridis','num_values':5}),('swagger','/swagger.json',None),('apidoc','/apidoc',None)]:
        request(id,path,query)
    request('metadata-batch','/metadata',method='POST',body={'keys':[['lagoon','red'],['dolomites','elevation']]})
    for r in recipes:
        categorical=r['id']=='landcover'
        query={'colormap':'explicit','explicit_color_map':json.dumps(PALETTE)} if categorical else {'colormap':'viridis'} if r['id']=='dolomites' else {'stretch_range':'[0,3000]'}
        prefix='/singleband/'+'/'.join(r['keys'])
        request(r['id']+'-preview',prefix+'/preview.png',query,view=dict(subset=r['id'],location=r['location'],kind='preview',settings=query))
        array=driver.get_raster_tile(r['keys'],tile_size=(256,256),preserve_values=categorical)
        name='arrays/'+r['id']+'.npz'
        np.savez_compressed(out/name,values=array.data,mask=np.ma.getmaskarray(array)); record['arrays'].append(name)
        with rasterio.open(CACHE/'subsets'/(r['id']+'.tif')) as src:
            w,s,e,n=transform_bounds(src.crs,'EPSG:4326',*src.bounds)
        tile=mercantile.tile((w+e)/2,(s+n)/2,12)
        for shift in (0,1):
            path=f'{prefix}/{tile.z}/{tile.x+shift}/{tile.y}.png'
            request(r['id']+f'-tile-{shift}',path,query,view=dict(subset=r['id'],location=r['location'],xyz=[tile.x+shift,tile.y,tile.z],kind='adjacent tile',settings=query))
    rgb={'r':'red','g':'green','b':'blue','r_range':'[0,3000]','g_range':'[0,3000]','b_range':'[0,3000]'}
    request('rgb','/rgb/lagoon/preview.png',rgb,view=dict(subset='lagoon-red/green/blue',location='Venice lagoon',kind='RGB preview',settings=rgb))
    ndvi={'expression':'(v1-v2)/(v1+v2)','v1':'nir','v2':'red','stretch_range':'[-1,1]','colormap':'viridis'}
    request('ndvi','/compute/lagoon/preview.png',ndvi,view=dict(subset='lagoon-nir/red',location='Venice lagoon',kind='compute preview',settings=ndvi))
    request('nonsquare','/singleband/lagoon/red/preview.png',{'tile_size':'[128,256]'})
    request('out-of-bounds','/singleband/lagoon/red/20/0/0.png',{'tile_size':'[128,512]'},view=dict(subset='lagoon-red',location='Outside footprint',kind='legacy empty tile dimensions',settings={'tile_size':[128,512]}))
    request('missing-dataset','/singleband/absent/red/preview.png',expected=404)
    request('invalid-json','/singleband/lagoon/red/preview.png',{'tile_size':'['},expected=400)
    request('invalid-keys','/datasets',{'unknown':'value'},expected=400)
    request('missing-rgb-band','/rgb/lagoon/preview.png',{'r':'red'},expected=400)
    request('bad-expression','/compute/lagoon/preview.png',{'expression':'nope(v1)','v1':'red','stretch_range':'[0,1]'},expected=400)
    request('percentile-p98','/singleband/lagoon/red/preview.png',{'stretch_range':'["p2","p98"]'})
    request('percentile-p99','/singleband/lagoon/red/preview.png',{'stretch_range':'["p2","p99"]'},expected=400)
    request('post-limit','/metadata',method='POST',body={'keys':[['lagoon','red']]*101},expected=400)
    request('method-error','/keys',method='POST',expected=405)
    request('missing-route','/does-not-exist',expected=404)
    # Warm/cached-OS samples only; raster application cache remains disabled.
    times=[]
    for _ in range(5):
        start=time.perf_counter(); response=client.get('/singleband/lagoon/red/preview.png'); assert response.status_code==200
        times.append(time.perf_counter()-start)
    write_json(out/'capture.json',record); write_json(out/'views.json',views)
    write_json(out/'timings.json',dict(cache='OS warm, application raster cache disabled',repetitions=5,seconds=times,median_seconds=statistics.median(times),requests=timings,claim='Flask test-client context only; not a production benchmark'))
    page=['<!doctype html><meta charset="utf-8"><title>P00 Python reference</title><style>body{font-family:sans-serif}article{display:inline-block;vertical-align:top;width:530px;margin:8px}img{image-rendering:auto;background:#bbb}pre{white-space:pre-wrap}</style><h1>P00 Python reference</h1><p>Native 256px display. See views.json for exact coordinates, settings, and subset IDs. Full source attribution and terms in ../assets.json.</p>']
    for v in views:
        page.append(f'<article><h2>{html.escape(v["id"])}</h2><img width="256" height="256" src="{v["image"]}"><pre>{html.escape(json.dumps(v,indent=2))}</pre></article>')
    (out/'index.html').write_text('\n'.join(page))
    # Small ordinary-scale overview for a reproducible inspection artifact.
    selected=['lagoon-red-preview','dolomites-preview','landcover-preview','rgb','ndvi','lagoon-mask-preview']
    sheet=Image.new('RGB',(3*256,2*280),'white')
    from PIL import ImageDraw
    draw=ImageDraw.Draw(sheet)
    for i,id in enumerate(selected):
        with Image.open(out/f'images/{id}.png') as im:
            rgba=im.convert('RGBA'); base=Image.new('RGBA',im.size,'#cccccc'); base.alpha_composite(rgba)
            sheet.paste(base.convert('RGB'),((i%3)*256,(i//3)*280+24))
        draw.text(((i%3)*256+4,(i//3)*280+4),id,fill='black')
    sheet.save(out/'contact-sheet.png')
    print(json.dumps({'requests':len(contract['requests']),'images':len(record['images']),'arrays':len(record['arrays']),'out':str(out)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--out',type=Path,required=True)
    capture(p.parse_args().out.resolve())
