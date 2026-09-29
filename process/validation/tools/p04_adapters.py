"""P04 packaged Python/WSGI and Lambda full-request adapter contract checks."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

import numpy as np
from PIL import Image
import terracotta as tc
from terracotta.server import create_app
from common import ROOT,COMMIT,write_json
from p02_validate import SETTINGS


def run(bundle,package,out):
    out.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(package));import terracotta_rust as rust
    from terracotta_rust.wsgi import create_app as create_wsgi
    database=ROOT/'process/validation/artifacts/capture-reproduction/reference.sqlite'
    tc.update_settings(DRIVER_PATH=str(database),**SETTINGS);oracle=create_app().test_client();wsgi=create_wsgi(str(database));results=[]
    scenarios=[
        ('compute','GET','/compute/lagoon/preview.png?'+urlencode({'expression':'(v1-v2)/(v1+v2)','v1':'nir','v2':'red','stretch_range':'[-1,1]','colormap':'viridis'}),None,{}),
        ('batch','POST','/metadata?columns=["range","metadata"]',{'keys':[['lagoon','red'],['lagoon','blue']]},{}),
        ('batch-duplicate','POST','/metadata',{'keys':[['lagoon','red'],['lagoon','red']]},{}),
        ('batch-invalid','POST','/metadata',{'keys':'x'},{}),
        ('batch-media','POST','/metadata',{}, {'Content-Type':'text/plain'}),
        ('head','HEAD','/keys',None,{}),
        ('preflight','OPTIONS','/metadata',None,{'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'Content-Type, X-Test'}),
        ('docs','GET','/apidoc',None,{}),
        ('spec','GET','/swagger.json',None,{}),
        ('denied-origin','GET','/singleband/lagoon/red/preview.png',None,{'Origin':'https://untrusted.test'}),
        ('error','GET','/compute/lagoon/preview.png?expression=v1',None,{}),
    ]
    for name,method,url,body,headers in scenarios:
        headers=dict({'Content-Type':'application/json','Origin':'http://localhost:8080'},**headers)
        data=json.dumps(body).encode() if body is not None else b''
        expected=oracle.open(url,method=method,headers=headers,data=data)
        candidates={}
        status,ctype,content,cache,extra=rust.request_full(database,method,url,headers,data)
        candidates['python']=(status,dict({'Content-Type':ctype},**({'Cache-Control':'no-cache'} if cache else {}),**extra),content)
        path,_,query=url.partition('?');environ={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'CONTENT_TYPE':headers['Content-Type'],'CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data)}
        environ.update({'HTTP_'+k.upper().replace('-','_'):v for k,v in headers.items() if k!='Content-Type'})
        start=[];content=b''.join(wsgi(environ,lambda status,hs:start.append((status,hs))))
        candidates['wsgi']=(int(start[0][0].split()[0]),dict(start[0][1]),content)
        for version in [1,2]:
            event={'headers':headers,'body':base64.b64encode(data).decode(),'isBase64Encoded':True}
            if version==2:event.update(version='2.0',rawPath=path,rawQueryString=query,requestContext={'http':{'method':method}})
            else:
                from urllib.parse import parse_qsl
                event.update(httpMethod=method,path=path,queryStringParameters=dict(parse_qsl(query)))
            event_path=out/f'{name}-v{version}.json';write_json(event_path,event)
            p=subprocess.run([str(bundle/'bin/terracotta'),'lambda-event','-d',str(database),'--event',str(event_path)],cwd=ROOT,capture_output=True,check=True)
            result=json.loads(p.stdout);content=base64.b64decode(result['body']) if result['isBase64Encoded'] else result['body'].encode()
            candidates[f'lambda-v{version}']=(result['statusCode'],result['headers'],content)
        for adapter,(status,hs,data) in candidates.items():
            errors=[];hs={k.lower():v for k,v in hs.items()}
            if status!=expected.status_code:errors.append(f'status {status} != {expected.status_code}')
            for h in ['content-type','cache-control','access-control-allow-origin','access-control-allow-methods','vary']:
                if hs.get(h)!=expected.headers.get(h):errors.append(f'header {h}: {hs.get(h)} != {expected.headers.get(h)}')
            if method=='HEAD':
                if data:errors.append('HEAD body not empty')
                if int(hs.get('content-length','0'))<=0:errors.append('HEAD length lost')
            elif expected.content_type=='image/png':
                a=np.asarray(Image.open(io.BytesIO(expected.data)).convert('RGBA'));b=np.asarray(Image.open(io.BytesIO(data)).convert('RGBA'))
                if not np.array_equal(a,b):errors.append('decoded image differs')
                (out/f'{name}-{adapter}.png').write_bytes(data);(out/f'{name}-python-reference.png').write_bytes(expected.data)
            elif status<400 and expected.is_json:
                if json.loads(data)!=expected.json:errors.append('JSON differs')
            elif status<400 and data!=expected.data:errors.append('text differs')
            results.append(dict(case=name,adapter=adapter,passed=not errors,errors=errors))
            if errors:print(name,adapter,errors)
    with ThreadPoolExecutor(4) as pool:
        statuses=list(pool.map(lambda _:rust.request_full(database,'GET','/singleband/lagoon/red/preview.png')[0],range(12)))
    results.append(dict(case='concurrent-app-startup',passed=statuses==[200]*12,statuses=statuses))
    results.append(dict(case='four-item-request-compatibility',passed=len(rust.request(database,'GET','/keys'))==4))
    report=dict(reference_commit=COMMIT,results=results,summary=dict(passed=sum(r['passed'] for r in results),failed=sum(not r['passed'] for r in results),total=len(results)))
    write_json(out/'report.json',report);print(report['summary'])
    if report['summary']['failed']:raise SystemExit(1)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--package',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();run(a.bundle.resolve(),a.package.resolve(),a.out.resolve())
