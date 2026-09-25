"""Packaged in-process SQL preparation and HTTP boundary checks."""
import io,sys,uuid
from pathlib import Path
import numpy as np
from PIL import Image
import p06_validate as p
p.OUT=p.ROOT/'validation/artifacts/p06-package';p.OUT.mkdir(exist_ok=True)
from p06_validate import *
sys.path.insert(0,os.environ.get('TC_P06_PACKAGE',str(ROOT/'validation/artifacts/p06-portable-python')))
import terracotta_rust as rust

def run():
    for provider in BASE:
        url=BASE[provider]+'/p06_'+uuid.uuid4().hex
        store=rust.DataStore(url,provider=provider);store.create(['scene','band'],key_descriptions={'scene':'packaged'})
        store.insert(['lagoon','red'],RASTER,metadata=META)
        check(provider+' packaged Python metadata',same_metadata(store.get_metadata(['lagoon','red']),META))
        check(provider+' packaged Python->oracle',same_metadata(tc.get_driver(url).get_metadata(['lagoon','red']),META))
        try:store.batch([dict(operation='delete',keys=['lagoon','red']),dict(operation='delete',keys=['missing','red'])]);failed=False
        except rust.TerracottaError:failed=True
        check(provider+' packaged batch rollback',failed and same_metadata(store.get_metadata(['lagoon','red']),META))
        response=rust.request(url,'GET','/singleband/lagoon/red/preview.png?stretch_range=[98,14568]')
        check(provider+' packaged render',response[0]==200 and np.array_equal(np.asarray(Image.open(io.BytesIO(response[2])).convert('RGBA')),np.asarray(Image.open(ROOT/'validation/artifacts/p06'/f'{provider}-reference.png').convert('RGBA'))))
        store.delete(['lagoon','red'])
        check(provider+' packaged delete',not tc.get_driver(url).get_datasets())
    write_json(OUT/'report.json',REPORT);print(len(REPORT['checks']),'packaged SQL checks passed')
if __name__=='__main__':run()
