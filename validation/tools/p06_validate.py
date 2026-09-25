"""P06 differential tests: real engines on disposable loopback Docker ports.
Run with the frozen oracle environment. Creates only p06_* databases.
"""
import concurrent.futures
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid

import numpy as np
import requests
import sqlalchemy as sa
import terracotta as tc
from click.testing import CliRunner
from terracotta.scripts import cli
from common import ROOT,COMMIT,write_json,sha256

OUT=ROOT/'validation/artifacts/p06'
OUT.mkdir(parents=True,exist_ok=True)
BIN=Path(os.environ.get('TC_P06_BINARY',ROOT/'target/debug/terracotta'))
REPORT={'reference_commit':COMMIT,'checks':[],'external':{'real_s3':'not-run: no credentials/account provided'}}
BASE={'mysql':'mysql://root:p06-disposable@127.0.0.1:53316','postgresql':'postgresql://postgres:p06-disposable@127.0.0.1:55436'}

def check(name,condition,details=None):
    REPORT['checks'].append(dict(name=name,passed=bool(condition),details=details));write_json(OUT/'report.json',REPORT)
    if not condition:raise AssertionError((name,details))

def command(args,success=True,input=None,env=None):
    p=subprocess.run([str(BIN),*map(str,args)],cwd=ROOT,text=True,input=input,capture_output=True,env=env)
    if success and p.returncode:raise RuntimeError((args,p.stdout,p.stderr))
    return p

def operation(path,op,success=True,**kwargs):
    req=dict(path=str(path),operation=op,**kwargs)
    with tempfile.NamedTemporaryFile('w',suffix='.json',dir=OUT) as f:
        json.dump(req,f);f.flush();p=command(['data-operation',f.name],success)
    return json.loads(p.stdout) if p.returncode==0 else p

def normal(m):
    return {k:np.asarray(v,dtype='float32').tolist() if k=='percentiles' else np.asarray(v).tolist() if k in ['range','bounds'] else v for k,v in m.items() if k!='keys'}

def same_metadata(a,b):
    from decimal import Decimal
    def equal(a,b):
        if isinstance(a,Decimal): return str(a)==format(float(b),'.10f')
        if isinstance(b,Decimal): return str(b)==format(float(a),'.10f')
        if isinstance(a,dict):return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
        if isinstance(a,(list,tuple)):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
        return a==b
    return equal(normal(a),normal(b))

def clean_metadata():
    source=tc.get_driver(str(ROOT/'validation/artifacts/capture-reproduction/reference.sqlite'))
    return normal(source.get_metadata(('lagoon','red'))),next(v for k,v in source.get_datasets().items() if k==('lagoon','red'))

META,RASTER=clean_metadata()

class Server:
    def __init__(self,path,env=None):
        import socket
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
        self.log=tempfile.TemporaryFile('w+')
        self.p=subprocess.Popen([str(BIN),'serve','-d',str(path),'--bind',f'127.0.0.1:{self.port}'],cwd=ROOT,stdout=self.log,stderr=self.log,env=env)
        self.url=f'http://127.0.0.1:{self.port}'
        for _ in range(200):
            if self.p.poll() is not None:self.log.seek(0);raise RuntimeError(self.log.read())
            try:
                requests.get(self.url+'/keys',timeout=.5);break
            except requests.ConnectionError:time.sleep(.05)
        else:raise RuntimeError('server not ready')
    def get(self,path):return requests.get(self.url+path,timeout=40)
    def close(self):self.p.terminate();self.p.wait(timeout=10);self.log.close()

def engine_case(provider):
    name='p06_'+uuid.uuid4().hex
    url=BASE[provider]+'/'+name
    source=tc.get_driver(url)
    source.create(['scene','band'],key_descriptions={'scene':'Unicode café 🪴','band':'channel'})
    source.insert(('lagoon','red'),RASTER,metadata=META)
    check(provider+' Python->Rust keys',operation(url,'keys')==[{'key':'scene','description':'Unicode café 🪴'},{'key':'band','description':'channel'}])
    check(provider+' Python->Rust metadata',normal(operation(url,'metadata',keys=['lagoon','red']))==META)
    operation(url,'insert',keys=['lagoon','blue'],raster=RASTER,metadata=META)
    check(provider+' Rust->Python metadata',normal(source.get_metadata(('lagoon','blue')))==META)
    operation(url,'insert',keys=['lagoon','blue'],raster='s3://example/new.tif',skip_metadata=True)
    check(provider+' skip overwrite stale metadata',normal(source.get_metadata(('lagoon','blue')))==META and source.get_datasets()[('lagoon','blue')]=='s3://example/new.tif')
    # SQL text and arbitrary string key values must stay parameterized.
    tricky="x' ? ; \\ 😀"
    operation(url,'insert',keys=[tricky,'red'],raster=RASTER,metadata=META)
    check(provider+' bound key literals',normal(source.get_metadata((tricky,'red')))==META)
    # Batch failure must undo successful earlier mutations.
    p=operation(url,'batch',False,operations=[dict(operation='delete',keys=['lagoon','red']),dict(operation='delete',keys=['missing','red'])])
    check(provider+' batch rollback',p.returncode!=0 and ('lagoon','red') in source.get_datasets())
    # Inject second-table failures to prove path + metadata atomicity.
    with source.meta_store.sqla_engine.begin() as c:
        if provider=='mysql':
            c.execute(sa.text("CREATE TRIGGER p06_fail BEFORE INSERT ON metadata FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='p06 injected'"))
        else:
            c.execute(sa.text("CREATE FUNCTION p06_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'p06 injected'; END $$"))
            c.execute(sa.text("CREATE TRIGGER p06_fail BEFORE INSERT ON metadata FOR EACH ROW EXECUTE FUNCTION p06_fail()"))
    before=source.get_datasets()[('lagoon','red')]
    p=operation(url,'insert',False,keys=['lagoon','red'],raster='bad',metadata=META)
    check(provider+' overwrite second-table rollback',p.returncode!=0 and source.get_datasets()[('lagoon','red')]==before and normal(source.get_metadata(('lagoon','red')))==META)
    with source.meta_store.sqla_engine.begin() as c:c.execute(sa.text('DROP TRIGGER p06_fail'+(' ON metadata' if provider=='postgresql' else '')))
    operation(url,'delete',keys=[tricky,'red'])
    check(provider+' delete both tables',not source.get_datasets({'scene':tricky}) and source.meta_store.get_metadata(dict(scene=tricky,band='red')) is None)
    # Engine-specific case/accent collation and ordering, compared on the same engine.
    for key in ['Alpha','zebra','éclair','delta','Beta']:
        source.insert((key,'red'),RASTER,metadata=META)
    for where in [{},{'scene':['alpha','ÉCLAIR']},{'band':['RED']},{'scene':[]}]:
        for order in [['scene'],['band','scene'],['path','scene'],[]]:
            for asc in [True,False]:
                expected=[{'keys':list(k),'path':v} for k,v in source.get_datasets(where,order_by=order,ascending=asc).items()]
                actual=operation(url,'datasets',where=where,order_by=order,ascending=asc)
                check(provider+f' query {where}/{order}/{asc}',actual==expected)
    for page in [0,1,2]:
        actual=operation(url,'datasets',order_by=['scene'],ascending=False,limit=2,page=page)
        expected=[{'keys':list(k),'path':v} for k,v in source.get_datasets(order_by=['scene'],ascending=False,limit=2,page=page).items()]
        check(provider+' SQL pagination ties '+str(page),actual==expected)
    # Separate processes race one lazy row; audit confirms one publication.
    source.insert(('lazy','red'),RASTER,skip_metadata=True)
    with source.meta_store.sqla_engine.begin() as c:
        c.execute(sa.text('CREATE TABLE p06_audit (n INTEGER)'))
        if provider=='mysql':c.execute(sa.text('CREATE TRIGGER p06_audit_insert AFTER INSERT ON metadata FOR EACH ROW INSERT INTO p06_audit VALUES (1)'))
        else:
            c.execute(sa.text('CREATE FUNCTION p06_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO p06_audit VALUES (1); RETURN NEW; END $$'))
            c.execute(sa.text('CREATE TRIGGER p06_audit_insert AFTER INSERT ON metadata FOR EACH ROW EXECUTE FUNCTION p06_audit()'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:operation(url,'metadata',keys=['lazy','red']),range(6)))
    with source.meta_store.sqla_engine.connect() as c:count=c.execute(sa.text('SELECT COUNT(*) FROM p06_audit')).scalar()
    check(provider+' concurrent lazy once',count==1 and all(m==results[0] for m in results))
    source.insert(('badlazy','red'),'/missing/p06.tif',skip_metadata=True)
    p=operation(url,'metadata',False,keys=['badlazy','red'])
    check(provider+' failed lazy rollback',p.returncode!=0 and source.meta_store.get_metadata(dict(scene='badlazy',band='red')) is None)
    operation(url,'insert',keys=['badlazy','red'],raster=RASTER,skip_metadata=True)
    check(provider+' failed lazy retry',operation(url,'metadata',keys=['badlazy','red'])['percentiles']==results[0]['percentiles'])
    source.insert(('readonly','red'),RASTER,skip_metadata=True)
    check(provider+' read-only lazy',operation(url,'metadata',False,keys=['readonly','red'],read_only=True).returncode!=0)
    server=Server(url)
    try:
        from urllib.parse import urlencode
        tc.drivers._DRIVER_CACHE.clear()
        for params in [dict(scene='alpha'),dict(ascending='false',order_by='[scene]',limit=3,page=1)]:
            from terracotta.server.app import create_app
            tc.update_settings(DRIVER_PATH=url,DRIVER_PROVIDER=provider)
            oracle=create_app().test_client().get('/datasets?'+urlencode(params))
            r=server.get('/datasets?'+urlencode(params))
            check(provider+' HTTP collation '+str(params),r.status_code==oracle.status_code and r.json()==oracle.json,{'actual':r.json(),'expected':oracle.json})
        oracle=create_app().test_client().get('/metadata/lagoon/red')
        r=server.get('/metadata/lagoon/red')
        check(provider+' HTTP metadata types',r.status_code==oracle.status_code and r.json()==oracle.json,{'actual':r.json(),'expected':oracle.json})
        if provider=='mysql':
            r=server.get('/singleband/lagoon/red/preview.png')
            oracle=create_app().test_client().get('/singleband/lagoon/red/preview.png')
            check('MySQL preserved Decimal default-stretch error',r.status_code==oracle.status_code==500)
        r=server.get('/singleband/lagoon/red/preview.png?stretch_range=[98,14568]')
        from PIL import Image
        from terracotta.server.app import create_app
        oracle=create_app().test_client().get('/singleband/lagoon/red/preview.png?stretch_range=[98,14568]')
        (OUT/(provider+'-reference.png')).write_bytes(oracle.data)
        (OUT/(provider+'-rust.png')).write_bytes(r.content)
        check(provider+' HTTP real raster image',r.status_code==200 and np.array_equal(np.asarray(Image.open(io.BytesIO(r.content)).convert('RGBA')),np.asarray(Image.open(io.BytesIO(oracle.data)).convert('RGBA'))))
    finally:server.close()
    # Native creates a database/schema Python can subsequently use.
    created=BASE[provider]+'/p06_'+uuid.uuid4().hex
    operation(created,'create',keys=[dict(key='scene',description='source'),dict(key='band',description='')])
    operation(created,'insert',keys=['lagoon','red'],raster=RASTER,metadata=META)
    py=tc.get_driver(created)
    check(provider+' native schema Python read',same_metadata(py.get_metadata(('lagoon','red')),META))
    py.insert(('lagoon','nir'),RASTER,metadata=META)
    check(provider+' native schema Python write',normal(operation(created,'metadata',keys=['lagoon','nir']))==META)
    REPORT.setdefault('engines',{})[provider]={'version':str(source.meta_store.sqla_engine.connect().execute(sa.text('SELECT VERSION()')).scalar()),'database':name}
    write_json(OUT/'report.json',REPORT)

def migrations():
    fixture=ROOT/'.cache/reference/tests/data/db-v075.sqlite'
    for target in ['0.8','0.9','0.10']:
        py=OUT/f'python-{target}.sqlite';rust=OUT/f'rust-{target}.sqlite'
        shutil.copyfile(fixture,py);shutil.copyfile(fixture,rust)
        result=CliRunner().invoke(cli.cli,['migrate',str(py),'--to',target,'--yes'])
        check('oracle migration '+target,result.exit_code==0,str(result.exception))
        command(['migrate',rust,'--to',target,'--yes'])
        with sqlite3.connect(py) as a,sqlite3.connect(rust) as b:
            check('migration schema/data '+target,list(a.iterdump())==list(b.iterdump()))
        check('migration no-op '+target,'Already at target' in command(['migrate',rust,'--to',target]).stdout)
    denied=OUT/'declined.sqlite';shutil.copyfile(fixture,denied);before=sha256(denied)
    p=command(['migrate',denied],False,input='n\n')
    check('migration declined unchanged',p.returncode!=0 and sha256(denied)==before)
    check('migration future rejected',command(['migrate',denied,'--to','0.11','-y'],False).returncode!=0 and sha256(denied)==before)
    check('migration downgrade rejected',command(['migrate',denied,'--from','0.10','--to','0.9','-y'],False).returncode!=0)
    command(['migrate',denied,'--from','v0.7.5'],input='yes\n')
    check('migration confirmation explicit from',operation(denied,'keys')[0]['key']=='key1')
    # Fail after successful DDL; transaction must leave version + old schema intact.
    bad=OUT/'failed.sqlite';shutil.copyfile(fixture,bad)
    with sqlite3.connect(bad) as c:c.execute('ALTER TABLE metadata DROP COLUMN stdev')
    with sqlite3.connect(bad) as c:before=list(c.iterdump())
    p=command(['migrate',bad,'-y'],False)
    with sqlite3.connect(bad) as c:after=list(c.iterdump())
    check('migration DDL failure rollback',p.returncode!=0 and before==after)
    migrated=tc.get_driver(str(denied));k=next(iter(migrated.get_datasets()))
    check('historical metadata preserved',normal(operation(denied,'metadata',keys=list(k)))==normal(migrated.get_metadata(k)))
    operation(denied,'insert',keys=['new','raster','red'],raster=RASTER,metadata=META)
    check('historical Rust write Python read',normal(migrated.get_metadata(('new','raster','red')))==META)

if __name__=='__main__':
    migrations()
    for p in BASE:engine_case(p)
    print(f"{len(REPORT['checks'])} P06 checks passed")
