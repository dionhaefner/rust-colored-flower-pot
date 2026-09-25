"""Engine historical upgrade and partial-DDL evidence on disposable copies."""
import uuid,sqlite3
import sqlalchemy as sa
from click.testing import CliRunner
from terracotta.scripts import cli
import p06_validate as p
p.OUT=p.ROOT/'validation/artifacts/p06-migrations';p.OUT.mkdir(exist_ok=True)
from p06_validate import tc,BASE,META,RASTER,operation,command,check,normal,same_metadata,REPORT,write_json,OUT


def run():
    for provider in BASE:
        copies=[]
        for implementation in ['python','rust']:
            url=BASE[provider]+'/p06_'+uuid.uuid4().hex
            d=tc.get_driver(url);d.create(['scene','band']);d.insert(['lagoon','red'],RASTER,metadata=META)
            with d.meta_store.sqla_engine.begin() as c:
                c.execute(sa.text("UPDATE terracotta SET version='0.8.0'"))
            if implementation=='python':
                result=CliRunner().invoke(cli.cli,['migrate',url,'-y'])
                check(provider+' Python 0.8 chain',result.exit_code==0,str(result.exception))
            else:command(['migrate',url,'-y'])
            tc.drivers._DRIVER_CACHE.clear();d=tc.get_driver(url)
            copies.append(normal(d.get_metadata(['lagoon','red'])))
            check(provider+' migrated version '+implementation,d.db_version=='0.10.0')
            if implementation=='rust':
                operation(url,'insert',keys=['new','red'],raster=RASTER,metadata=META)
                check(provider+' migrated Rust write Python read',same_metadata(d.get_metadata(['new','red']),META))
        check(provider+' migration language equivalence',same_metadata(copies[0],copies[1]) and same_metadata(copies[0],META))
        # Failing mid-chain: no version advance; PG rolls back DDL, MySQL keeps additions.
        url=BASE[provider]+'/p06_'+uuid.uuid4().hex
        d=tc.get_driver(url);d.create(['scene','band']);d.insert(['lagoon','red'],RASTER,metadata=META)
        with d.meta_store.sqla_engine.begin() as c:
            c.execute(sa.text("UPDATE terracotta SET version='0.8.0'"));c.execute(sa.text('ALTER TABLE metadata DROP COLUMN stdev'))
        before=[col['name'] for col in sa.inspect(d.meta_store.sqla_engine).get_columns('metadata')]
        result=command(['migrate',url,'-y'],False)
        after=[col['name'] for col in sa.inspect(d.meta_store.sqla_engine).get_columns('metadata')]
        with d.meta_store.sqla_engine.connect() as c:version=c.execute(sa.text('SELECT version FROM terracotta')).scalar()
        check(provider+' failed migration unchanged marker',result.returncode!=0 and version=='0.8.0')
        check(provider+' DDL failure semantics',after==before if provider=='postgresql' else all(k+'_' in after for k in ['stdev','mean','min']))
        # Import the actual historical fixture into the engine, preserving data/order.
        url=BASE[provider]+'/p06_'+uuid.uuid4().hex
        admin=tc.get_driver(BASE[provider]+('/mysql' if provider=='mysql' else '/postgres')).meta_store.sqla_engine
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:c.execute(sa.text('CREATE DATABASE '+url.rsplit('/',1)[1]))
        d=tc.get_driver(url);engine=d.meta_store.sqla_engine
        with sqlite3.connect(p.ROOT/'.cache/reference/tests/data/db-v075.sqlite') as src,engine.begin() as dst:
            for table in ['terracotta','keys','datasets','metadata']:
                meta=sa.MetaData();cols=[];rows=src.execute('SELECT * FROM "'+table+'"').fetchall()
                for _,name,typ,_,_,pk in src.execute('PRAGMA table_info("'+table+'")'):
                    datatype=sa.LargeBinary if typ=='BLOB' else sa.Float(53) if typ=='REAL' else sa.String(8000) if name in ['filepath','convex_hull','metadata','description'] else sa.String(60)
                    cols.append(sa.Column(name,datatype,primary_key=bool(pk)))
                target=sa.Table(table,meta,*cols);meta.create_all(dst)
                if rows:dst.execute(target.insert(),[dict(zip(target.c.keys(),r)) for r in rows])
        result=command(['migrate',url,'-y'],False)
        if provider=='mysql':
            # Frozen SQL is invalid on MySQL; exact same first failure from oracle.
            oracle=CliRunner().invoke(cli.cli,['migrate',url,'-y'])
            check('MySQL legacy 0.7 unsupported SQL preserved',result.returncode!=0 and oracle.exit_code!=0)
            with engine.connect() as c:check('MySQL 0.7 failed marker retained',c.execute(sa.text('SELECT version FROM terracotta')).scalar()=='0.7.5')
        else:
            check('PostgreSQL actual historical import upgrades',result.returncode==0,result.stderr)
            keys=operation(url,'keys');check('PostgreSQL historical key order',[k['key'] for k in keys]==['key1','akey','key2'])
            datasets=operation(url,'datasets');k=datasets[0]['keys']
            check('PostgreSQL historical metadata readable',normal(operation(url,'metadata',keys=k))==normal(d.get_metadata(k)))
    write_json(OUT/'report.json',REPORT);print(len(REPORT['checks']),'engine migration checks passed')
if __name__=='__main__':run()
