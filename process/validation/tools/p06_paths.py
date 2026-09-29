"""Provider, credentials, path normalization, reconnect and write-failure cases."""
import os,uuid
import sqlalchemy as sa
import p06_validate as p
p.OUT=p.ROOT/'process/validation/artifacts/p06-paths';p.OUT.mkdir(exist_ok=True)
from p06_validate import *

def run():
    for provider in ['mysql','postgresql']:
        from terracotta.drivers import load_driver
        cls=load_driver(provider)
        for path in [f'{provider}://root@localhost:5000/test','root@localhost:5000/test',f'{provider}://root:secret@localhost/test',f'{provider}://localhost/test','localhost/test']:
            r=operation(path,'provider_info',provider=provider)
            check(provider+' normalization '+path.replace('secret','<redacted>'),r['identity']==cls._normalize_path(path) and 'secret' not in str(r))
        for path in ['http://localhost/test',f'{provider}://localhost',f'{provider}://localhost/test/foo/bar']:
            check(provider+' invalid path '+path,operation(path,'provider_info',False,provider=provider).returncode!=0)
        name='p06_'+uuid.uuid4().hex;url=BASE[provider]+'/'+name
        operation(url,'create',keys=[dict(key='scene',description=''),dict(key='band',description='')])
        operation(url,'insert',keys=['lagoon','red'],raster=RASTER,metadata=META)
        d=tc.get_driver(url)
        # Settings fallback and explicit provider for scheme-less URLs.
        stripped=url.split('@')[1]
        saved={k:os.environ.get(k) for k in ['TC_SQL_USER','TC_SQL_PASSWORD']}
        os.environ.update(TC_SQL_USER='root' if provider=='mysql' else 'postgres',TC_SQL_PASSWORD='p06-disposable')
        try:check(provider+' settings credentials',operation(stripped,'keys',provider=provider)==operation(url,'keys'))
        finally:
            for k,v in saved.items():
                if v is None:os.environ.pop(k,None)
                else:os.environ[k]=v
        bad=url.replace('p06-disposable','wrong-p06-password')
        result=operation(bad,'keys',False)
        check(provider+' invalid credentials redacted',result.returncode!=0 and 'wrong-p06-password' not in result.stderr)
        reader='p06_reader_'+uuid.uuid4().hex[:12]
        operation(url,'insert',keys=['readonly','red'],raster=RASTER,skip_metadata=True)
        with d.meta_store.sqla_engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            if provider=='mysql':
                c.execute(sa.text(f"CREATE USER '{reader}'@'%' IDENTIFIED BY 'p06-readonly'"))
                c.execute(sa.text(f"GRANT SELECT ON {name}.* TO '{reader}'@'%'"))
            else:
                c.execute(sa.text(f"CREATE ROLE {reader} LOGIN PASSWORD 'p06-readonly'"))
                c.execute(sa.text(f'GRANT CONNECT ON DATABASE {name} TO {reader}'))
                c.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO {reader}'))
                c.execute(sa.text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {reader}'))
        readonly=url.replace('root:p06-disposable',reader+':p06-readonly').replace('postgres:p06-disposable',reader+':p06-readonly')
        check(provider+' engine SELECT-only existing metadata',same_metadata(operation(readonly,'metadata',keys=['lagoon','red']),META))
        result=operation(readonly,'metadata',False,keys=['readonly','red'])
        check(provider+' engine SELECT-only lazy failure',result.returncode!=0 and d.meta_store.get_metadata(dict(scene='readonly',band='red')) is None)
        server=Server(url)
        try:
            check(provider+' before connection retirement',server.get('/datasets').status_code==200)
            with d.meta_store.sqla_engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
                if provider=='mysql':
                    ids=[r[0] for r in c.execute(sa.text('SELECT ID FROM information_schema.processlist WHERE DB=:db AND ID != CONNECTION_ID()'),dict(db=name))]
                    for n in ids:c.execute(sa.text(f'KILL CONNECTION {n}'))
                else:c.execute(sa.text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:db AND pid != pg_backend_pid()'),dict(db=name))
            check(provider+' reconnect stale connection',server.get('/datasets').status_code==200)
        finally:server.close()
        with d.meta_store.sqla_engine.begin() as c:
            if provider=='mysql':c.execute(sa.text("CREATE TRIGGER p06_delete BEFORE DELETE ON metadata FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='injected'"))
            else:
                c.execute(sa.text("CREATE FUNCTION p06_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$"))
                c.execute(sa.text('CREATE TRIGGER p06_delete BEFORE DELETE ON metadata FOR EACH ROW EXECUTE FUNCTION p06_delete()'))
        result=operation(url,'delete',False,keys=['lagoon','red'])
        check(provider+' delete second-table rollback',result.returncode!=0 and ('lagoon','red') in d.get_datasets() and same_metadata(d.get_metadata(['lagoon','red']),META))
        # CLI ingest into an existing relational store.
        command(['ingest',str(ROOT/'.cache/corpus/subsets/{scene}-{band}.tif'),'-o',url,'--skip-metadata','--skip-existing','-q'])
        check(provider+' relational CLI ingestion',len(operation(url,'datasets'))==6)
    base=OUT/'new.sqlite'
    for path in [str(base),str(OUT)+'/missing/../new.sqlite','sqlite:///'+str(base)]:
        from terracotta.drivers.sqlite_meta_store import SQLiteMetaStore
        check('sqlite normalize '+path,operation(path,'provider_info')['identity']==SQLiteMetaStore._normalize_path(path))
    for path,expected in [('s3://bucket/key','sqlite-remote'),('S3://bucket/key','sqlite-remote'),('POSTGRESQL://localhost/db','postgresql'),('mysql://host/db','mysql'),('postgresql://host/db','postgresql'),('local.sqlite','sqlite')]:
        check('auto provider '+expected,operation(path,'provider_info')['provider']==expected)
    check('empty path rejected',operation('','provider_info',False).returncode!=0)
    check('unknown provider rejected',operation('anything','provider_info',False,provider='invalid').returncode!=0)
    write_json(OUT/'report.json',REPORT);print(len(REPORT['checks']),'path and failure checks passed')
if __name__=='__main__':run()
