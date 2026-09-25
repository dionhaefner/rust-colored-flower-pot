"""Disposable native preparation/ingestion followed by frozen Python rollback."""
import io
import json
import shutil
import sqlite3
import subprocess
from urllib.parse import urlencode

import numpy as np
from PIL import Image
import terracotta as tc
from terracotta.server import create_app
from common import ROOT, COMMIT, sha256, write_json
from p08_runtime import BINARY, Service, fetch


def main():
    out=ROOT/'validation/artifacts/p08-rollback';out.mkdir(parents=True,exist_ok=False)
    source=ROOT/'.cache/corpus/subsets/lagoon-red.tif';db=out/'catalog.sqlite';checks=[]
    def command(*args):
        result=subprocess.run([str(BINARY),*map(str,args)],capture_output=True,text=True,check=True)
        return result
    def operation(request):
        file=out/'operation.json';write_json(file,request)
        return json.loads(command('data-operation',file).stdout)
    command('optimize-rasters',source,'-o',out/'prepared','--compression','deflate','--no-in-memory','-q')
    prepared=out/'prepared/lagoon-red.tif'
    command('ingest',out/'prepared/{scene}-{band}.tif','-o',db,'-q')
    driver=tc.get_driver(db)
    assert list(driver.get_keys())==['scene','band']
    assert driver.get_datasets()[('lagoon','red')]==str(prepared)
    checks.append('frozen-Python-reads-native-optimized-and-ingested-catalog')
    before=driver.get_metadata(('lagoon','red'))
    # A quiescent backup is taken before mutations/migrations, never a partial
    # copy of a live SQLite WAL database.
    backup=out/'before.sqlite'
    with sqlite3.connect(db) as live,sqlite3.connect(backup) as copy:live.backup(copy)
    backup_hash=sha256(backup)
    operation(dict(operation='insert',path=str(db),keys=['lagoon','copy'],raster=str(prepared),options={'extra_metadata':{'owner':'P08','unicode':'café'}}))
    operation(dict(operation='insert',path=str(db),keys=['lagoon','red'],raster=str(prepared),options={'extra_metadata':{'revision':2}}))
    operation(dict(operation='delete',path=str(db),keys=['lagoon','copy']))
    # Reopen through an independent oracle identity; no native process is needed.
    reopened=out/'rust-written.sqlite'
    with sqlite3.connect(db) as live,sqlite3.connect(reopened) as copy:live.backup(copy)
    old=tc.get_driver(reopened);metadata=old.get_metadata(('lagoon','red'))
    assert metadata['metadata']=={'revision':2}
    assert metadata['percentiles']==before['percentiles'] and old.db_version=='0.10.1'
    assert list(old.get_datasets())==[('lagoon','red')]
    checks.append('frozen-Python-reads-Rust-overwrite-delete-extra-metadata-and-float32-blobs')
    tc.update_settings(DRIVER_PATH=str(reopened),USE_MULTIPROCESSING=False,RASTER_CACHE_SIZE=0)
    path='/singleband/lagoon/red/preview.png?'+urlencode({'stretch_range':'[0,3000]'})
    ref=create_app().test_client().get(path)
    with Service(db,out/'server') as service:
        status,body=fetch(service.base,path)
    assert status==ref.status_code==200
    a=np.asarray(Image.open(io.BytesIO(ref.data)).convert('RGBA'));b=np.asarray(Image.open(io.BytesIO(body)).convert('RGBA'))
    assert np.array_equal(a,b)
    (out/'python-return.png').write_bytes(ref.data);(out/'rust.png').write_bytes(body)
    checks.append('return-to-frozen-Python-serves-exact-Rust-written-catalog-image')
    # Continuing to write in Python and returning to native also works.
    old.insert(('lagoon','python'),str(prepared),metadata=metadata)
    got=operation(dict(operation='metadata',path=str(reopened),keys=['lagoon','python']))
    assert got['metadata']==metadata['metadata'] and got['percentiles']==metadata['percentiles']
    checks.append('Python-after-rollback-write-readable-by-native')
    historic=out/'historical.sqlite';shutil.copy2(ROOT/'validation/evidence/p06/fixtures/db-v075.sqlite',historic)
    historic_backup=out/'historical-backup.sqlite';shutil.copy2(historic,historic_backup)
    original=sha256(historic_backup)
    command('migrate',historic,'--from','0.7.5','-y')
    # Migration chains write the minor release marker 0.10.0, while newly
    # created catalogs use 0.10.1. Compare the actual frozen migration contract.
    oracle_migrated=out/'historical-oracle.sqlite';shutil.copy2(historic_backup,oracle_migrated)
    from click.testing import CliRunner
    from terracotta.scripts import cli
    result=CliRunner().invoke(cli.cli,['migrate',str(oracle_migrated),'--from','0.7.5','-y'])
    assert result.exit_code==0,result.output
    migrated=tc.get_driver(historic);oracle=tc.get_driver(oracle_migrated)
    assert migrated.db_version==oracle.db_version=='0.10.0'
    assert migrated.get_keys()==oracle.get_keys() and migrated.get_datasets()==oracle.get_datasets()
    for keys in migrated.get_datasets():assert migrated.get_metadata(keys)==oracle.get_metadata(keys)
    restored=out/'historical-restored.sqlite';shutil.copy2(historic_backup,restored)
    assert sha256(restored)==original and sha256(backup)==backup_hash
    checks.append('historical-upgrade-read-by-current-Python-and-byte-exact-backup-restore')
    write_json(out/'report.json',dict(passed=True,reference_commit=COMMIT,checks=checks,
        ordinary_backup_sha256=backup_hash,historical_backup_sha256=original,
        scope='SQLite complete prepare/ingest/write/serve/return path. Real MySQL/PostgreSQL cross-language transaction/migration results are separate P08 engine reports. Historical restore preserves bytes; it does not claim the current 0.10 oracle can run a 0.7 schema.'))
    print('PASS',len(checks),'rollback checks')


if __name__=='__main__':main()
