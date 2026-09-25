"""Real P05 CLI serve and packaged Python preparation workflows."""
import concurrent.futures,io,json,os,sqlite3,subprocess,sys,time,urllib.request
from pathlib import Path
import numpy as np
from PIL import Image
import terracotta as tc
from terracotta.server import create_app
from common import ROOT,write_json
from p01_validate import wait_ready

OUT=ROOT/'validation/artifacts/p05-workflows';OUT.mkdir(parents=True,exist_ok=True)
BUNDLE=Path(os.environ.get('TC_P05_BUNDLE',ROOT/'validation/artifacts/p05-qualified'));PACKAGE=Path(os.environ.get('TC_P05_PACKAGE',ROOT/'validation/artifacts/p05-qualified-python'))
sys.path.insert(0,str(PACKAGE))
import terracotta_rust as rust
checks=[]
def check(name,ok):
    checks.append({'name':name,'passed':bool(ok)});write_json(OUT/'report.json',dict(checks=checks))
    assert ok,name
raster=ROOT/'.cache/corpus/subsets/lagoon-red.tif'
metadata=rust.compute_metadata(raster,extra_metadata={'nested':['extra',1]})
check('python-compute',metadata['metadata']=={'nested':['extra',1]} and isinstance(metadata['bounds'],tuple))
db=OUT/'python-native.sqlite';db.unlink(missing_ok=True)
d=rust.DataStore(db);d.create(['scene','band'],key_descriptions={'scene':'Scene'})
d.insert(['venice','red'],raster,metadata=metadata)
legacy=tc.get_driver(str(db));check('python-adapter-write',legacy.get_metadata(['venice','red'])==d.get_metadata(['venice','red']))
try:d.batch([dict(operation='delete',keys=['venice','red']),dict(operation='delete',keys=['absent','red'])])
except rust.TerracottaError:pass
else:raise AssertionError('batch did not fail')
check('python-adapter-rollback',('venice','red') in legacy.get_datasets())
d.insert(['lazy','red'],raster,skip_metadata=True)
with sqlite3.connect(db) as c:
    c.executescript("CREATE TABLE audit(n INTEGER); CREATE TRIGGER observe_insert AFTER INSERT ON metadata BEGIN INSERT INTO audit VALUES(1); END;")
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    results=list(pool.map(lambda _:d.get_metadata(['lazy','red']),range(8)))
check('python-concurrent-lazy',all(v==results[0] for v in results) and sqlite3.connect(db).execute('select count(*) from audit').fetchone()[0]==1)
d.insert(['broken','red'],'/nonexistent/p05.tif',skip_metadata=True)
try:d.get_metadata(['broken','red'])
except rust.TerracottaError:pass
else:raise AssertionError('missing raster did not fail')
check('failed-lazy-rollback',sqlite3.connect(db).execute("select count(*) from metadata where scene='broken'").fetchone()[0]==0)
d.insert(['broken','red'],raster,skip_metadata=True);check('failed-lazy-recovery',d.get_metadata(['broken','red'])==results[0])
d.delete(['broken','red']);check('python-adapter-delete',('broken','red') not in legacy.get_datasets())
# Trigger an SQLite failure after replacing the dataset, and verify both rows survive.
with sqlite3.connect(db) as c:c.executescript("CREATE TRIGGER reject_metadata BEFORE INSERT ON metadata WHEN NEW.scene='venice' BEGIN SELECT RAISE(ABORT, 'injected write failure'); END;")
try:d.insert(['venice','red'],raster,override_path='should-not-be-committed',metadata=metadata)
except rust.TerracottaError:pass
else:raise AssertionError('write trigger did not fail')
check('two-table-rollback',legacy.get_datasets()[('venice','red')]==str(raster) and legacy.get_metadata(['venice','red'])['metadata']==metadata['metadata'])
# Existing CLI pattern, RGB ordering, lazy HTTP metadata, tile and preview.
pattern=str(ROOT/'.cache/corpus/subsets/lagoon-{band}.tif')
port=5115;base=f'http://127.0.0.1:{port}'
with (OUT/'serve.log').open('w') as log:
    process=subprocess.Popen([str(BUNDLE/'bin/terracotta'),'serve','-r',pattern,'--rgb-key','band','--port',str(port)],stdout=log,stderr=log,cwd=ROOT)
    try:
        wait_ready(base,process)
        keys=json.load(urllib.request.urlopen(base+'/keys'));check('serve-pattern-keys',keys['keys'][0]['key']=='band')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses=list(pool.map(lambda _:json.load(urllib.request.urlopen(base+'/metadata/red')),range(8)))
        check('serve-concurrent-metadata',all(x==responses[0] for x in responses))
        ref=OUT/'reference.sqlite';ref.unlink(missing_ok=True);driver=tc.get_driver(str(ref));driver.create(['band']);driver.insert(['red'],str(raster),skip_metadata=True)
        tc.update_settings(DRIVER_PATH=str(ref),DRIVER_PROVIDER='sqlite',USE_MULTIPROCESSING=False)
        client=create_app().test_client()
        for url in ['/singleband/red/preview.png','/singleband/red/12/2188/1466.png','/singleband/red/preview.png?stretch_range=["p2","p98"]']:
            actual=urllib.request.urlopen(base+url).read();expected=client.get(url).data
            a=np.asarray(Image.open(io.BytesIO(actual)).convert('RGBA'));b=np.asarray(Image.open(io.BytesIO(expected)).convert('RGBA'))
            check('serve-visible-'+url,np.array_equal(a,b))
            if 'preview.png?' not in url:
                label='preview' if 'preview' in url else 'tile';Image.fromarray(a).save(OUT/f'{label}-rust.png');Image.fromarray(b).save(OUT/f'{label}-python.png')
    finally:process.terminate();process.wait(timeout=30)
write_json(OUT/'report.json',dict(passed=len(checks),checks=checks));print('PASS',len(checks))
