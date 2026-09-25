"""Retain small executable evidence for inspected reference write quirks."""
import json,sqlite3
from pathlib import Path
from click.testing import CliRunner
from terracotta.scripts.cli import cli
import terracotta as tc
from common import ROOT,write_json
out=ROOT/'validation/artifacts/p05-quirks';out.mkdir(parents=True,exist_ok=True)
db=out/'reference.sqlite';db.unlink(missing_ok=True);driver=tc.get_driver(str(db));driver.create(['name'])
raster=str(ROOT/'.cache/corpus/subsets/lagoon-red.tif');driver.insert(['image'],raster);before=driver.get_metadata(['image']);driver.insert(['image'],'replacement.tif',skip_metadata=True)
checks={'skip_metadata_retains_previous':driver.get_metadata(['image'])==before}
try:
    with driver.connect():
        driver.insert(['committed'],raster,metadata=before)
        raise RuntimeError('failure after nested insert')
except RuntimeError:pass
checks['nested_insert_commits_before_outer_failure']=('committed',) in driver.get_datasets()
folder=out/'files';folder.mkdir(exist_ok=True);file=folder/'a.tif'
if not file.exists():file.symlink_to(raster)
p=CliRunner().invoke(cli,['ingest',str(folder/'{renamed}.tif'),'-o',str(db),'--skip-metadata','-q'])
checks['same_length_key_mismatch_still_inserts']=p.exit_code==0 and ('a',) in driver.get_datasets() and 'incompatible key names' in p.output
assert all(checks.values()),checks
write_json(out/'report.json',{'passed':len(checks),'checks':checks,'mismatch_output':p.output});print('PASS',len(checks))
