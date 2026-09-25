"""Storage-backed HTTP/render modes against reopened relational oracle drivers."""
import io,json
from urllib.parse import urlencode
from PIL import Image
import numpy as np,requests
import p06_validate as p
p.OUT=p.ROOT/'validation/artifacts/p06-surfaces';p.OUT.mkdir(exist_ok=True)
from p06_validate import *

def run():
    engines=json.loads((ROOT/'validation/artifacts/p06/report.json').read_text())['engines']
    scenarios=[
        ('partial-stretch','/singleband/lagoon/red/preview.png',{'stretch_range':'[null,14568]'}),
        ('percentile','/singleband/lagoon/red/preview.png',{'stretch_range':'["p2","p98"]'}),
        ('categorical','/singleband/lagoon/red/preview.png',{'colormap':'explicit','explicit_color_map':'{"160":[255,0,0,255],"169":[0,255,0,255]}'}),
        ('rgb-default','/rgb/lagoon/preview.png',dict(r='red',g='red',b='red')),
        ('rgb-explicit','/rgb/lagoon/preview.png',dict(r='red',g='red',b='red',r_range='[98,14568]',g_range='[98,14568]',b_range='[98,14568]')),
        ('compute','/compute/lagoon/preview.png',dict(expression='v1/2',v1='red',stretch_range='[98,14568]')),
    ]
    for provider,engine in engines.items():
        url=BASE[provider]+'/'+engine['database']
        tc.drivers._DRIVER_CACHE.clear();tc.update_settings(DRIVER_PATH=url,DRIVER_PROVIDER=provider)
        from terracotta.server import create_app
        oracle=create_app().test_client();server=Server(url)
        try:
            for name,path,params in scenarios:
                uri=path+'?'+urlencode(params);a=oracle.get(uri);b=server.get(uri)
                same=a.status_code==b.status_code
                if same and a.status_code==200:
                    same=np.array_equal(np.asarray(Image.open(io.BytesIO(a.data)).convert('RGBA')),np.asarray(Image.open(io.BytesIO(b.content)).convert('RGBA')))
                    (OUT/f'{provider}-{name}-reference.png').write_bytes(a.data);(OUT/f'{provider}-{name}-rust.png').write_bytes(b.content)
                check(provider+' '+name,same,dict(reference_status=a.status_code,rust_status=b.status_code))
            a=oracle.post('/metadata',json=dict(keys=[['lagoon','red'],['lagoon','red']]))
            b=requests.post(server.url+'/metadata',json=dict(keys=[['lagoon','red'],['lagoon','red']]))
            check(provider+' batch metadata',a.status_code==b.status_code==200 and a.json==b.json())
        finally:server.close()
    write_json(OUT/'report.json',REPORT);print(len(REPORT['checks']),'backend surface checks passed')
if __name__=='__main__':run()
