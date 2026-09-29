"""P07 real-corpus Python/WSGI/settings/cache/operational assertions."""
import concurrent.futures, io, json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
from urllib.parse import urlencode
import numpy as np
from PIL import Image
from common import ROOT, write_json
import terracotta as tc
from terracotta_rust import NativeApplication
from terracotta_rust.wsgi import Application
OUT=Path(os.environ.get('TC_INTEGRATION_OUT',ROOT/'process/validation/artifacts/p07-integration'));OUT.mkdir(parents=True,exist_ok=True)
REPORT={'checks':[], 'reference_commit':'6db38d5873cde5ebabe47338a1f61ddc788eada6'}
DB=ROOT/'process/validation/artifacts/capture-reproduction/reference.sqlite'
BIN=Path(os.environ.get('TC_P07_BINARY',ROOT/'target/debug/terracotta')).resolve()

def check(name,condition,details=None):
    REPORT['checks'].append(dict(name=name,passed=bool(condition),details=details));write_json(OUT/'report.json',REPORT)
    if not condition:raise AssertionError((name,details))

def pixels(body):return np.asarray(Image.open(io.BytesIO(body)).convert('RGBA'))

def main():
    import terracotta_rust
    check('replacement-import',Path(tc.__file__).parent.parent==Path(terracotta_rust.__file__).parent.parent,tc.__file__)
    tc.update_settings(USE_MULTIPROCESSING=False)
    driver=tc.get_driver(DB)
    check('driver-cache',driver is tc.get_driver(str(DB.parent/'unused/..'/DB.name)))
    check('version',driver.db_version=='0.10.1')
    check('key-types',isinstance(driver.key_names,tuple) and list(driver.get_keys())==['scene','band'])
    raster=driver.get_datasets({"scene":"lagoon","band":"red"})[('lagoon','red')]
    # Oracle arrays and object types, captured in its own process.
    code='''import terracotta as tc, numpy as np, sys, json
D=tc.get_driver(sys.argv[1]); M=D.compute_metadata(sys.argv[2]); A=D.get_raster_tile(('lagoon','red'),tile_size=(64,96))
from terracotta.raster import get_raster_tile
G=get_raster_tile(sys.argv[2],target_crs='epsg:4326',tile_size=(64,96))
np.savez(sys.argv[3],values=A.data,mask=A.mask,percentiles=M['percentiles'],geographic=G.data,geographic_mask=G.mask)
print(json.dumps(dict(dtype=str(A.dtype),percentiles=type(M['percentiles']).__name__,bounds=type(M['bounds']).__name__)))
'''
    oracle_env=dict(os.environ,PYTHONPATH=str(ROOT/'.cache/reference'),TC_USE_MULTIPROCESSING='false')
    result=subprocess.run([str(ROOT/'.venv-reference/bin/python'),'-c',code,str(DB),raster,str(OUT/'oracle.npz')],env=oracle_env,capture_output=True,text=True,check=True)
    (OUT/'oracle-types.json').write_text(result.stdout)
    expected=np.load(OUT/'oracle.npz');a=driver.get_raster_tile(('lagoon','red'),tile_size=(64,96))
    check('real-array-dtype',str(a.dtype)==json.loads(result.stdout)['dtype'])
    check('real-array-values',np.array_equal(a.data,expected['values']))
    check('real-array-mask',np.array_equal(a.mask,expected['mask']))
    from terracotta.raster import get_raster_tile
    geographic=get_raster_tile(raster,target_crs='epsg:4326',tile_size=(64,96))
    check('public-target-crs',np.array_equal(geographic.data,expected['geographic']) and np.array_equal(geographic.mask,expected['geographic_mask']))
    m=driver.compute_metadata(raster)
    check('computed-metadata-types',isinstance(m['percentiles'],np.ndarray) and isinstance(m['bounds'],tuple))
    check('full-percentiles-f64',np.array_equal(m['percentiles'],expected['percentiles']))
    future=driver.get_raster_tile(('lagoon','red'),tile_size=(64,96),asynchronous=True)
    check('future-type',isinstance(future,concurrent.futures.Future))
    check('future-result',np.array_equal(future.result().data,a.data))
    with tempfile.TemporaryDirectory(dir=OUT) as folder:
        path=Path(folder)/'ingestion.sqlite';new=tc.get_driver(path);new.create(['scene','band'],key_descriptions={'scene':'Area'})
        try:
            with new.connect():
                new.insert({'band':'red','scene':'lagoon'},raster,metadata=m)
                raise RuntimeError('outer failure')
        except RuntimeError:pass
        check('legacy-context-commit',('lagoon','red') in new.get_datasets())
        import sqlalchemy as sqla
        with new.connect() as connection:
            check('public-sqlalchemy-connection',isinstance(connection,sqla.engine.Connection) and connection.execute(sqla.text('SELECT count(*) FROM datasets')).scalar()==1)
            with new.connect() as nested:
                check('nested-connection-identity',nested is connection)
        got=new.get_metadata(('lagoon','red'))
        check('stored-metadata-types',type(got['percentiles']) is list and type(got['range']) is tuple)
        check('stored-f32',got['percentiles']==m['percentiles'].astype('float32').tolist())
        new.delete(('lagoon','red'));check('delete',not new.get_datasets())
    url='/singleband/lagoon/red/preview.png?'+urlencode({'stretch_range':'[0,3000]'})
    app=NativeApplication(DB,dict(DEFAULT_TILE_SIZE=[64,96],RASTER_CACHE_SIZE=1048576))
    one=app.request('GET',url);two=app.request('GET',url)
    check('native-response',one[0]==200 and type(one[2]) is bytes)
    check('persistent-cache',json.loads(app.request('GET','/__terracotta/metrics')[2])['cache_hits']>=1)
    check('identical-cache-image',np.array_equal(pixels(one[2]),pixels(two[2])))
    apps=[NativeApplication(DB,dict(DEFAULT_TILE_SIZE=size,PNG_COMPRESS_LEVEL=level)) for size,level in [([32,48],0),([64,96],9)]]
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda i:apps[i%2].request('GET',url),range(12)))
    check('concurrent-settings-isolation',all(pixels(r[2]).shape[:2]==[(32,48),(64,96)][i%2] for i,r in enumerate(results)))
    levels=[NativeApplication(DB,dict(PNG_COMPRESS_LEVEL=level)).request('GET',url)[2] for level in (0,1,9)]
    check('png-levels-visible-equality',all(np.array_equal(pixels(levels[0]),pixels(x)) for x in levels))
    check('png-levels-effective',len(levels[0])>len(levels[1])>len(levels[2]),list(map(len,levels)))
    wsgi=Application(str(DB));env={'REQUEST_METHOD':'GET','PATH_INFO':'/singleband/lagoon/red/preview.png','QUERY_STRING':'stretch_range=%5B0%2C3000%5D','wsgi.input':io.BytesIO()}
    status=[];body=b''.join(wsgi(env,lambda s,h:status.append((s,h))))
    reference=(ROOT/'process/validation/reference/capture/images/lagoon-red-preview.png').read_bytes()
    check('wsgi-real-raster',status[0][0]=='200 OK' and np.array_equal(pixels(body),pixels(reference)))
    tc.update_settings(DRIVER_PATH=str(DB));from terracotta.server import create_app
    flask=create_app(profile=True)
    check('flask-profiler-real-request',flask.test_client().get('/keys').status_code==200)
    udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0));udp.settimeout(3)
    previous=os.environ.get('AWS_XRAY_DAEMON_ADDRESS');os.environ['AWS_XRAY_DAEMON_ADDRESS']='127.0.0.1:'+str(udp.getsockname()[1])
    try:
        traced=NativeApplication(DB,{'XRAY_PROFILE':True});traced.request('GET','/keys',{'X-Amzn-Trace-Id':'Root=1-12345678-123456789012345678901234;Parent=1234567890123456;Sampled=1'})
        packet=udp.recv(65536).decode();header,segment=packet.split('\n',1);segment=json.loads(segment)
        check('native-xray-daemon',json.loads(header)=={'format':'json','version':1} and segment['parent_id']=='1234567890123456' and segment['http']['response']['status']==200)
    finally:
        udp.close()
        if previous is None:os.environ.pop('AWS_XRAY_DAEMON_ADDRESS',None)
        else:os.environ['AWS_XRAY_DAEMON_ADDRESS']=previous
    write_json(OUT/'report.json',REPORT);print(f"{len(REPORT['checks'])} checks passed")
if __name__=='__main__':main()
