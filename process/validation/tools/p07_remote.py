"""Public remote driver through a real socket emulator, not boto monkeypatches."""
import gc, json, os, time
from pathlib import Path
import boto3
from moto.server import ThreadedMotoServer
from common import ROOT, write_json
import terracotta as tc
from terracotta import exceptions
from sqlalchemy.engine import Connection

OUT=Path(os.environ.get('TC_REMOTE_OUT',ROOT/'process/validation/artifacts/p07-remote'));OUT.mkdir(parents=True,exist_ok=True)
checks=[]
def check(name,value):
    checks.append(dict(name=name,passed=bool(value)))
    write_json(OUT/'report.json',dict(checks=checks))
    assert value,name
server=ThreadedMotoServer(ip_address='127.0.0.1',port=0,verbose=False);server.start()
host,port=server.get_host_and_port()
os.environ.update(AWS_ACCESS_KEY_ID='FakeKey',AWS_SECRET_ACCESS_KEY='FakeSecretKey',AWS_DEFAULT_REGION='us-east-1',AWS_ENDPOINT_URL_S3=f'http://{host}:{port}')
try:
    tc.update_settings(REMOTE_DB_CACHE_TTL=1,REMOTE_DB_CACHE_DIR=str(OUT/'cache'),USE_MULTIPROCESSING=False)
    client=boto3.client('s3',endpoint_url=f'http://{host}:{port}')
    client.create_bucket(Bucket='p07-tests')
    source=OUT/'source.sqlite'
    if source.exists():source.unlink()
    local=tc.get_driver(source);local.create(['scene','band'])
    raster=ROOT/'.cache/corpus/subsets/lagoon-red.tif'
    local.insert(('lagoon','red'),str(raster))
    def upload():client.put_object(Bucket='p07-tests',Key='tc.sqlite',Body=source.read_bytes())
    upload()
    remote=tc.get_driver('s3://p07-tests/tc.sqlite')
    check('eager-download',Path(remote.meta_store._local_path).is_file())
    check('public-keys',remote.key_names==('scene','band'))
    check('public-metadata-types',isinstance(remote.get_metadata(('lagoon','red'))['percentiles'],list))
    for name,call in [('create',lambda:remote.create(['x'])),('insert',lambda:remote.insert(('lagoon','red'),str(raster))),('delete',lambda:remote.delete(('lagoon','red')))]:
        try:call()
        except exceptions.DatabaseNotWritableError:check('read-only-'+name,True)
        else:check('read-only-'+name,False)
    with remote.connect() as connection:
        check('public-connection-type',isinstance(connection,Connection))
        first=remote.meta_store._local_path
        local.insert(('lagoon','blue'),str(raster));upload();time.sleep(1.1)
        check('context-pins-generation',len(remote.get_datasets())==1 and remote.meta_store._local_path==first)
    check('refresh-on-reconnect',len(remote.get_datasets())==2)
    check('old-generation-retired',not Path(first).exists())
    final=remote.meta_store._local_path
    remote.meta_store.__del__();gc.collect()
    check('snapshot-owner-release',not Path(final).exists())
    for path,error in [('not-an-s3-url',ValueError),('s3://p07-tests/missing.sqlite',exceptions.InvalidDatabaseError)]:
        try:tc.get_driver(path,provider='sqlite-remote')
        except error:check('eager-error-'+path,True)
        else:check('eager-error-'+path,False)
finally:server.stop()
print(f'{len(checks)} remote adapter checks passed')
