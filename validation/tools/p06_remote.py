"""S3 protocol emulator and immutable generation tests (not real AWS)."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from moto.server import ThreadedMotoServer
import boto3
import requests
import p06_validate
p06_validate.OUT = p06_validate.ROOT/'validation/artifacts/p06-remote'
p06_validate.OUT.mkdir(exist_ok=True)
from p06_validate import ROOT,OUT,Server,operation,META,RASTER,check,REPORT,write_json


def run():
    shutil.rmtree(OUT/"remote-cache",ignore_errors=True)
    emulator=ThreadedMotoServer(ip_address='127.0.0.1',port=0,verbose=False)
    emulator.start()
    host,port=emulator.get_host_and_port()
    endpoint=f'http://{host}:{port}'
    env=dict(os.environ,AWS_ACCESS_KEY_ID='FakeKey',AWS_SECRET_ACCESS_KEY='FakeSecretKey',AWS_SESSION_TOKEN='FakeToken',AWS_DEFAULT_REGION='us-east-1',AWS_ENDPOINT_URL_S3=endpoint,TC_REMOTE_DB_CACHE_TTL='1',TC_REMOTE_DB_CACHE_DIR=str(OUT/'remote-cache'))
    client=boto3.client('s3',endpoint_url=endpoint,aws_access_key_id='FakeKey',aws_secret_access_key='FakeSecretKey',aws_session_token='FakeToken',region_name='us-east-1')
    client.create_bucket(Bucket='p06-test')
    local=OUT/'remote-source.sqlite'
    if local.exists():local.unlink()
    operation(local,'create',keys=[dict(key='scene',description='generation one'),dict(key='band',description='')])
    operation(local,'insert',keys=['lagoon','red'],raster=RASTER,metadata=META)
    operation(local,'insert',keys=['lazy','red'],raster=RASTER,skip_metadata=True)
    def upload():client.put_object(Bucket='p06-test',Key='tc.sqlite',Body=local.read_bytes())
    upload()
    server=None
    try:
        server=Server('s3://p06-test/tc.sqlite',env)
        check('S3 initial read',server.get('/datasets').status_code==200)
        check('S3 lazy read-only failure',server.get('/metadata/lazy/red').status_code==403)
        first=list((OUT/'remote-cache').glob('*.sqlite'))
        check('one live snapshot',len(first)==1)
        operation(local,'insert',keys=['lagoon','blue'],raster=RASTER,metadata=META)
        with sqlite3.connect(local) as c:c.execute("UPDATE key_names SET description='generation two' WHERE key_name='scene'")
        upload()
        check('S3 TTL caches initial generation',len(server.get('/datasets').json()['datasets'])==2)
        time.sleep(1.1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:responses=list(pool.map(lambda _:server.get('/datasets'),range(24)))
        check('S3 concurrent refresh complete data',all(r.status_code==200 and len(r.json()['datasets'])==3 for r in responses))
        check('S3 refreshed key definitions',server.get('/keys').json()['keys'][0]['description']=='generation two')
        second=list((OUT/'remote-cache').glob('*.sqlite'))
        check('retired connection removed',len(second)==1 and first[0]!=second[0] and not first[0].exists())
        good=second[0].read_bytes()
        client.put_object(Bucket='p06-test',Key='tc.sqlite',Body=b'not a database')
        time.sleep(1.1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:failed=list(pool.map(lambda _:server.get('/datasets').status_code,range(8)))
        check('concurrent invalid refresh fails',failed==[500]*8)
        check('invalid refresh preserves good snapshot',second[0].read_bytes()==good and len(list((OUT/'remote-cache').glob('*.sqlite')))==1)
        upload()
        check('refresh failure retries without TTL advance',server.get('/datasets').status_code==200)
        client.delete_object(Bucket='p06-test',Key='tc.sqlite')
        time.sleep(1.1)
        check('missing remote object failure',server.get('/datasets').status_code==500)
        upload()
        check('missing object recovery',server.get('/datasets').status_code==200)
        from PIL import Image
        import numpy as np,io
        remote=server.get('/singleband/lagoon/red/preview.png')
        local_server=Server(local)
        try:native=local_server.get('/singleband/lagoon/red/preview.png')
        finally:local_server.close()
        check('S3 local render equivalence',remote.status_code==native.status_code==200 and np.array_equal(np.asarray(Image.open(io.BytesIO(remote.content))),np.asarray(Image.open(io.BytesIO(native.content)))))
        # Run direct data operations with endpoint settings; never writes remotely.
        import p06_validate
        previous=dict(os.environ)
        try:
            os.environ.update(env)
            oracle=p06_validate.tc.get_driver('s3://p06-test/tc.sqlite')
            check('S3 Python reads Rust snapshot',p06_validate.same_metadata(oracle.get_metadata(['lagoon','red']),META))
            for op,kw in [('create',dict(keys=[dict(key='x',description='')])),('insert',dict(keys=['lagoon','red'],raster=RASTER,metadata=META)),('delete',dict(keys=['lagoon','red'])),('batch',dict(operations=[]))]:
                result=operation('s3://p06-test/tc.sqlite',op,False,**kw)
                check('S3 read-only '+op,result.returncode!=0 and 'not writable' in result.stderr)
        finally:os.environ.clear();os.environ.update(previous)
    finally:
        if server:server.close()
        emulator.stop()
    write_json(OUT/'remote-report.json',REPORT)
    print(f"{len(REPORT['checks'])} emulator checks passed")
if __name__=='__main__':run()
