"""Expanded frozen-Python versus native HTTP contract and image matrix (offline inputs)."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from urllib.parse import urlencode, quote
import urllib.request
import urllib.error

import numpy as np
from PIL import Image, ImageDraw
import terracotta as tc
from terracotta.server import create_app
from terracotta.cmaps import AVAILABLE_CMAPS

from common import ROOT, COMMIT, write_json
from p01_validate import wait_ready
from p02_validate import SETTINGS, clone_row, create_partial_mask_raster

HEADERS = ['content-type', 'cache-control', 'access-control-allow-origin', 'access-control-allow-methods', 'access-control-allow-headers', 'vary', 'allow']


def cases():
    result=[]
    def add(name,path,query=None,method='GET',body=None,origin='http://localhost:8080',headers=None):
        h={'Origin':origin,'Content-Type':'application/json'} if origin else {'Content-Type':'application/json'}
        h.update(headers or {})
        result.append(dict(id=name,url=path+('?' + urlencode(query) if query else ''),method=method,body=body,headers=h))
    sb='/singleband/lagoon/red/preview.png';rgb='/rgb/lagoon/preview.png';comp='/compute/lagoon/preview.png'
    for path in ['/keys','/datasets','/metadata/lagoon/red','/swagger.json','/apidoc','/static/favicon.ico','/static/redoc.standalone.js']:
        add('route-'+path.replace('/','-'),path)
    for name,q in [ ('multi',{'band':'[red,blue]'}),('literal-comma',{'band':'red,blue'}),('quoted-list',{'band':'["red","blue"]'}),('empty-list',{'band':'[]'}),('combo',{'scene':'lagoon','band':'[red,blue]'}),('ordered',{'order_by':'[band,scene]'}),('descending',{'ascending':'FALSE'}),('page',{'limit':2,'page':1}),('empty-page',{'page':100}),('zero-limit',{'limit':0}),('invalid-limit',{'limit':-1}),('invalid-page',{'page':'x'}),('invalid-order',{'order_by':'[bad]'}),('string-order',{'order_by':'band'}),('path-order',{'order_by':'[path]'}),('invalid-bool',{'ascending':'maybe'}),('unknown-filter',{'unknown':'x'})]:add('datasets-'+name,'/datasets',q)
    add('duplicate-query','/datasets?scene=lagoon&scene=missing')
    for columns in [['range'],['mean','stdev','metadata'],[],['bad'],['keys']]:add('columns-'+str(columns),'/metadata/lagoon/red',{'columns':json.dumps(columns)})
    for name,body in [('batch',{'keys':[['lagoon','red'],['lagoon','blue']]}),('duplicates',{'keys':[['lagoon','red']]*2}),('empty',{'keys':[]}),('limit',{'keys':[['lagoon','red']]*101}),('missing',{'keys':[['lagoon','missing']]}),('bad-count',{'keys':[['lagoon']]}),('no-keys',{}),('array',[]),('unknown-field',{'keys':[],'other':1}),('wrong-type',{'keys':'bad'})]:add('metadata-'+name,'/metadata',method='POST',body=body)
    add('batch-columns','/metadata',{'columns':'["range","metadata"]'},'POST',{'keys':[['lagoon','red'],['lagoon','blue']]})
    add('metadata-media','/metadata',method='POST',body={},headers={'Content-Type':'text/plain'})
    add('metadata-unknown','/metadata/lagoon/red',{'bad':'1'})
    for name in AVAILABLE_CMAPS:
        add('palette-'+name,sb,{'colormap':name})
        add('legend-'+name,'/colormap',{'colormap':name,'stretch_range':'[-3,9]','num_values':9})
    for n in [0,1,2,255,300]:add('legend-gray-'+str(n),'/colormap',{'stretch_range':'[-3,9]','num_values':n})
    for q in [{},{'stretch_range':'[0,1]','colormap':'bad'},{'stretch_range':'[1,1]','num_values':3},{'stretch_range':'[9,1]','num_values':3}]:add('legend-option-'+str(q),'/colormap',q)
    for q in [{'stretch_range':'[null,null]'},{'stretch_range':'["p0","p98"]'},{'stretch_range':'[1,1]'},{'stretch_range':'[3000,0]'},{'stretch_range':'null'},{'stretch_range':'[0,"p99"]'},{'stretch_range':'[0,"p100"]'},{'stretch_range':'[0,"p-1"]'},{'colormap':'explicit'},{'explicit_color_map':'{"10":[1,2,3]}'},{'colormap':'bogus'},{'tile_size':'[64,96]'},{'tile_size':'[64]'},{'tile_size':'bad'},{'unexpected':'ignored'}]:add('single-option-'+str(q),sb,q)
    palette={'10':[0,100,0,255],'20':'#ffbb22','30':[255,255,76],'40':[240,150,255,128],'50':[250,0,0],'60':[180,180,180],'80':[0,100,200],'90':[0,150,160],'95':[0,207,117],'100':[250,230,160]}
    for transform in [None,'gamma 1 2']:
        q={'colormap':'explicit','explicit_color_map':json.dumps(palette)}
        if transform:q['color_transform']=transform
        add('categorical-'+str(transform),'/singleband/lagoon/classes/preview.png',q)
    for name,transform in [('gamma','gamma 1 1.5'),('sigmoid','sigmoidal 1 15 0.5'),('inverse','sigmoidal 1 -5 0.3'),('zero','sigmoidal 1 0 0'),('bias-zero','sigmoidal 1 5 0'),('chain','gamma 1 1.5, sigmoidal 1 15 0.5'),('uppercase','GAMMA R 1.2'),('bad','gamma 1 -1'),('band','gamma rgb 2'),('unknown','foo 1 2'),('saturation','saturation 1.4')]:add('single-transform-'+name,sb,{'color_transform':transform})
    base={'r':'red','g':'green','b':'blue','r_range':'[0,3000]','g_range':'[0,3000]','b_range':'[0,3000]'}
    for name,transform in [('gamma','gamma rgb 1.5'),('channels','gamma g 0.8 gamma b 0.7'),('sigmoid','sigmoidal rgb 10 0.4'),('inverse','sigmoidal rgb -5 0.3'),('saturation','saturation 1.5'),('gray','saturation 0'),('chain','gamma rgb 1.5, sigmoidal rgb 10 0.5, saturation 1.3'),('bad','gamma 4 1')]:
        add('rgb-transform-'+name,rgb,dict(base,color_transform=transform))
    add('rgb-partial-saturation','/rgb/partial/preview.png',dict(base,color_transform='saturation 1.5'))
    add('rgb-missing-channel',rgb,{'r':'red','g':'green'})
    add('rgb-reversed',rgb,dict(base,r_range='[3000,0]'))
    add('rgb-percentile',rgb,dict(base,r_range='["p0","p98"]',g_range='[null,3000]',b_range='[0,null]'))
    add('rgb-null',rgb,dict(base,r_range='null'))
    expressions=['(v1-v2)/(v1+v2)','where(v1>v2,1,0)','setmask(v1,getmask(v2))','masked_where(v1<1000,v1)','sqrt(v1-1000)','v1*0/0','-v1**2','v1**-1','where(v1>1000,nan,1)','getmask(v2)','minimum(v1,v2)','sin(v1*pi/3000)']
    for i,e in enumerate(expressions):add('compute-'+str(i),comp,{'expression':e,'v1':'nir','v2':'red','stretch_range':'[-1,1]' if i==0 else '[0,3000]','colormap':'viridis'})
    add('compute-tile','/compute/lagoon/12/2188/1466.png',{'expression':expressions[0],'v1':'nir','v2':'red','stretch_range':'[-1,1]','tile_size':'[128,256]'})
    add('compute-outside','/compute/lagoon/20/0/0.png',{'expression':'v1','v1':'red','stretch_range':'[0,1]','tile_size':'[128,512]'})
    add('compute-five-inputs',comp,{'expression':'v1+v2+v3+v4+v5','v1':'red','v2':'green','v3':'blue','v4':'nir','v5':'red','stretch_range':'[0,15000]'})
    for e in ['0','v100','v1+','v1.size','[1]*999999999','v1<v1<3','v1&v1','mod(v1)','mod(v1,2)','where(v1,2)']:
        add('compute-error-'+e,comp,{'expression':e,'v1':'red','stretch_range':'[0,1]'})
    for q in [{},{'expression':'v1'},{'expression':'v1','v1':'red','stretch_range':'[1,0]'},{'expression':'v1','v1':'red','stretch_range':'[0,1]','colormap':'explicit'},{'expression':'v1','v1':'red','stretch_range':'["p1",1]'}]:add('compute-options-'+str(q),comp,q)
    for path in ['/keys','/metadata','/missing',sb,'/singleband/lagoon/red/no.png']:
        for method in ['HEAD','OPTIONS','POST','GET']:
            add('method-'+method+'-'+path,path,method=method)
    for origin in [None,'https://foreign.example','http://127.0.0.1:8080','https://localhost:99']:
        for path in ['/keys',sb,'/swagger.json','/missing']:
            add('cors-'+str(origin)+'-'+path,path,origin=origin)
    add('preflight','/metadata',method='OPTIONS',headers={'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'Content-Type, X-Test'})
    add('url-unicode','/metadata/'+quote('a+b %é',safe='')+'/red')
    add('url-literal-plus','/metadata/a+b%20%25%C3%A9/red')
    add('url-slash','/metadata/a%2Fb/red')
    add('explicit-float-colors','/singleband/lagoon/classes/preview.png',{'colormap':'explicit','explicit_color_map':'{"10":[-1,256.5,100.9,128.3]}'})
    add('explicit-empty','/singleband/lagoon/classes/preview.png',{'colormap':'explicit','explicit_color_map':'{}'})
    add('rgb-partial-chain','/rgb/partial/preview.png',dict(base,color_transform='gamma rgb 1.5, sigmoidal rgb 10 0.5, saturation 1.3'))
    add('compute-dem-log','/compute/dolomites/preview.png',{'expression':'log(v1)','v1':'elevation','stretch_range':'[6,9]'})
    add('compute-dem-exp','/compute/dolomites/preview.png',{'expression':'exp(v1/1000)','v1':'elevation','stretch_range':'[0,15]'})
    add('compute-dem-sin','/compute/dolomites/preview.png',{'expression':'sin(v1)','v1':'elevation','stretch_range':'[-1,1]'})
    for scene,band in [('lagoon','classes'),('mask','red')]:
        add('category-zero-'+scene,f'/singleband/{scene}/{band}/preview.png',{'colormap':'explicit','explicit_color_map':'{"0":[255,0,0],"10":[0,100,0]}'})
    add('outside-invalid-option','/singleband/lagoon/red/0/0/0.png',{'color_transform':'garbage'})
    add('compute-numeric-strings',comp,{'expression':'v1','v1':'red','stretch_range':'["0","3000"]'})
    add('legend-numeric-strings','/colormap',{'stretch_range':'["0","1"]','num_values':3})
    add('single-size-strings',sb,{'tile_size':'["64","96"]'})
    add('single-size-floats',sb,{'tile_size':'[64.5,96.5]'})
    add('single-no-key','/singleband/preview.png')
    add('single-no-tile-key','/singleband/0/0/0.png')
    add('compute-scalar-zero',comp,{'expression':'v1+(1/0)','v1':'red','stretch_range':'[0,1]'})
    add('explicit-valid-floats','/singleband/lagoon/classes/preview.png',{'colormap':'explicit','explicit_color_map':'{"10":[1.5,120.7,255]}'})
    import mercantile
    for zoom in [31,35,64,1024]:
        tile=mercantile.tile(12.3,45.4,zoom) if zoom<1024 else mercantile.Tile(0,0,zoom)
        add('high-zoom-'+str(zoom),f'/singleband/lagoon/red/{zoom}/{tile.x}/{tile.y}.png')
    return result


def capture_response(status,headers,body):
    headers={k.lower():v for k,v in headers.items() if k.lower() in HEADERS}
    record={'status':status,'headers':headers,'sha256':hashlib.sha256(body).hexdigest()}
    if headers.get('content-type','').startswith('application/json'):
        record['json']=json.loads(body) if body else None
    elif not headers.get('content-type','').startswith('image/'):
        record['text']=body.decode(errors='replace')
    return record


def run(out,port):
    out.mkdir(parents=True,exist_ok=False)
    for p in ['reference','rust']: (out/p).mkdir()
    database=out/'database.sqlite'
    shutil.copy2(ROOT/'process/validation/artifacts/capture-reproduction/reference.sqlite',database)
    partial=out/'partial.tif'; create_partial_mask_raster(partial)
    with sqlite3.connect(database) as connection:
        for scene in ['a+b %é','a/b','partial']:
            for band in ['red','green','blue']:
                clone_row(connection,'datasets',('lagoon',band),(scene,band),str(partial) if scene=='partial' and band=='red' else None)
                clone_row(connection,'metadata',('lagoon',band),(scene,band))
    tc.update_settings(DRIVER_PATH=str(database),**SETTINGS)
    app=create_app();app.logger.disabled=True;client=app.test_client()
    matrix=cases();results=[];reference=[]
    if os.environ.get("P04_FOCUS"): matrix=[c for c in matrix if not c["id"].startswith(("palette-","legend-")) or c["id"] in ["palette-viridis","legend-viridis"]]
    with (out/'server.log').open('w') as log:
        process=subprocess.Popen([str(Path(os.environ.get('TC_P04_BINARY',ROOT/'target/debug/terracotta'))),'serve','-d',str(database),'--bind',f'127.0.0.1:{port}'],stdout=log,stderr=subprocess.STDOUT,cwd=ROOT)
        try:
            wait_ready(f'http://127.0.0.1:{port}',process)
            handshake=subprocess.run([str(ROOT/'.venv-reference/bin/terracotta'),'connect',f'http://127.0.0.1:{port}','--no-browser','--port','5199'],env=dict(os.environ,TC_TESTING='1'),capture_output=True,text=True,timeout=20)
            write_json(out/'old-client.json',{'command':handshake.args,'returncode':handshake.returncode,'stdout':handshake.stdout,'stderr':handshake.stderr,'note':'Unchanged legacy connect executes real /keys and /swagger.json handshake. TC_TESTING suppresses its persistent client server; render contracts are exercised by this matrix.'})
            if handshake.returncode:raise RuntimeError(handshake.stderr)
            for index,case in enumerate(matrix):
                data=None if case['body'] is None else json.dumps(case['body']).encode()
                expected=client.open(case['url'],method=case['method'],data=data,headers=case['headers'])
                ref=capture_response(expected.status_code,expected.headers,expected.data);reference.append(dict(case,response=ref))
                req=urllib.request.Request(f'http://127.0.0.1:{port}'+case['url'],data=data,method=case['method'],headers=case['headers'])
                try:actual=urllib.request.urlopen(req,timeout=120)
                except urllib.error.HTTPError as e:actual=e
                body=actual.read();got=capture_response(actual.status,actual.headers,body)
                errors=[]
                if got['status']!=ref['status']:errors.append(f"status {got['status']} != {ref['status']}")
                for key in HEADERS:
                    x,y=got['headers'].get(key),ref['headers'].get(key)
                    if key in ['allow','access-control-allow-headers']:
                        x=set(v.strip().lower() for v in x.split(',')) if x else x;y=set(v.strip().lower() for v in y.split(',')) if y else y
                    if x!=y:errors.append(f'header {key}: {x!r} != {y!r}')
                if ref['status']<400 and 'json' in ref and got.get('json')!=ref['json']:errors.append('JSON differs')
                if ref['status']>=400 and 'json' in ref:
                    message=got.get('json',{}).get('message','')
                    if not isinstance(message,str) or not message:errors.append('missing error meaning')
                    expected_message=ref['json']['message'].lower()
                    for phrase in ['no dataset found','percentile','expression','color transform','stretch','tile_size','boolean','maximum number of keys']:
                        if phrase in expected_message and phrase not in message.lower():errors.append('missing error meaning: '+phrase)
                if ref['headers'].get('content-type')=='image/png' and case['method']!='HEAD':
                    (out/'reference'/f'{index}.png').write_bytes(expected.data)
                    (out/'rust'/f'{index}.png').write_bytes(body)
                    try:
                        a=np.asarray(Image.open(io.BytesIO(expected.data)).convert('RGBA'));b=np.asarray(Image.open(io.BytesIO(body)).convert('RGBA'))
                        if a.shape!=b.shape:errors.append(f'image shape {b.shape} != {a.shape}')
                        elif not np.array_equal(a,b):errors.append(f'image channels differ: {np.count_nonzero(a!=b)}, max {np.abs(a.astype(int)-b).max()}')
                    except Exception as e:errors.append(str(e))
                elif ref['status']<400 and 'text' in ref and got.get('text')!=ref['text']:errors.append('text differs')
                result={'id':case['id'],'index':index,'passed':not errors,'errors':errors,'actual':got};results.append(result)
                if errors: print(case['id'],errors,flush=True)
        finally:process.terminate();process.wait(timeout=10)
    write_json(out/'reference.json',{'commit':COMMIT,'requests':reference})
    write_json(out/'partial-results.json',results)
    selected=[i for i,c in enumerate(matrix) if c['id'] in ['palette-viridis','single-transform-chain','rgb-transform-saturation','rgb-partial-saturation','compute-0','categorical-None']]
    sheet=Image.new('RGB',(512,len(selected)*280),'white');draw=ImageDraw.Draw(sheet)
    for row,i in enumerate(selected):
        for col,folder in enumerate(['reference','rust']):
            im=Image.open(out/folder/f'{i}.png').convert('RGBA');sheet.paste(im,(col*256,row*280),im)
        draw.text((3,row*280+257),matrix[i]['id']+' Python | Rust',fill='black')
    sheet.save(out/'side-by-side.png')
    summary={'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),'total':len(results)}
    write_json(out/'report.json',{'summary':summary,'results':results,'reference_commit':COMMIT,'binary_sha256':hashlib.sha256(Path(os.environ.get('TC_P04_BINARY',ROOT/'target/debug/terracotta')).read_bytes()).hexdigest()})
    print(summary)
    if summary['failed']:raise SystemExit(1)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--port',type=int,default=5104);a=p.parse_args();run(a.out.resolve(),a.port)
