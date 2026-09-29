"""Compare displayed metadata stretches from native versus oracle statistics."""
import base64,io,json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
import terracotta as tc
from terracotta.raster import compute_metadata
from common import ROOT,write_json
import p05_validate as v
v.OUT=ROOT/'process/validation/artifacts/p05-metadata-images';v.OUT.mkdir(parents=True,exist_ok=True);v.BINARY=ROOT/'target/release/terracotta'
views=[]
for name in ['lagoon-red','lagoon-mask','dolomites','landcover']:
    raster=ROOT/'.cache/corpus/subsets'/f'{name}.tif'
    for mode,options in [('full',{}),('sample',dict(max_shape=[173,291])),('chunk',dict(use_chunks=True))]:
        native=v.operation(dict(operation='compute_metadata',path=str(raster),options=options));oracle=compute_metadata(str(raster),**options)
        for stretch in ['default','percentile']:
            images=[]
            for label,metadata in [('python',oracle),('rust',native)]:
                prefix=f'{name}-{mode}-{stretch}-{label}';db=v.OUT/(prefix+'.sqlite');db.unlink(missing_ok=True);driver=tc.get_driver(str(db));driver.create(['name']);driver.insert(['image'],str(raster),metadata=metadata)
                event=v.OUT/(prefix+'.json');write_json(event,dict(httpMethod='GET',path='/singleband/image/preview.png',queryStringParameters={'stretch_range':'["p2","p98"]'} if stretch=='percentile' else {}))
                response=json.loads(v.command(['lambda-event','-d',db,'--event',event]).stdout);v.check(prefix+'-status',response['statusCode']==200)
                image=Image.open(io.BytesIO(base64.b64decode(response['body']))).convert('RGBA');image.save(v.OUT/(prefix+'.png'));images.append(image)
            v.check(f'{name}-{mode}-{stretch}-visible',np.array_equal(np.asarray(images[0]),np.asarray(images[1])))
            if mode=='full' and stretch=='percentile':views.append((name,images))
sheet=Image.new('RGB',(512,len(views)*280),'white');draw=ImageDraw.Draw(sheet)
for row,(name,images) in enumerate(views):
    draw.text((5,row*280+3),name+' — Python | Rust',fill='black')
    for col,image in enumerate(images):sheet.paste(image,(col*256,row*280+24),image)
sheet.save(v.OUT/'side-by-side.png');v.REPORT['passed']=len(v.REPORT['checks']);write_json(v.OUT/'report.json',v.REPORT);print('PASS',v.REPORT['passed'])
