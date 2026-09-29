"""Run the combined final P08 qualification with explicit exit accounting.

Requires prepared final packages, frozen oracle/corpus, and disposable SQL
engines already started. No service skips, artifact builds or implicit downloads.
Run separately from the isolated benchmark. --resume retains completed check records.
"""
import argparse
import datetime
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from common import ROOT, write_json


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--resume',action='store_true');a=p.parse_args()
    evidence=ROOT/'process/validation/evidence/p08';manifest=evidence/'qualification.json'
    rows=json.loads(manifest.read_text())['checks'] if a.resume and manifest.exists() else []
    python=str(ROOT/'.venv-reference/bin/python');bundle='process/validation/artifacts/p08-final';package='process/validation/artifacts/p08-final-python';binary=bundle+'/bin/terracotta'
    base=dict(os.environ,PYTHONPATH=str(ROOT/'.cache/reference'))
    native=dict(base,PYTHONPATH=str(ROOT/package),TC_P07_BINARY=str(ROOT/binary))
    jobs=[
      ('fmt',['packaging/cargo.sh','fmt','--check'],base),
      ('clippy',['packaging/cargo.sh','clippy','--locked','--offline','--all-targets','--','-D','warnings'],base),
      ('native',['packaging/cargo.sh','test','--locked','--offline','--features','storage-integration'],dict(base,RCFP_TEST_MYSQL_URL='mysql://root:p06-disposable@127.0.0.1:53316',RCFP_TEST_POSTGRESQL_URL='postgresql://postgres:p06-disposable@127.0.0.1:55436')),
      ('http',[python,'process/validation/tools/p04_validate.py','--out','process/validation/artifacts/p08-http-final'],dict(base,TC_P04_BINARY=binary)),
      ('adapters',[python,'process/validation/tools/p04_adapters.py','--bundle',bundle,'--package',package,'--out','process/validation/artifacts/p08-adapters-final'],base),
      ('upstream',[python,'-m','pytest','.cache/reference/tests','--ignore=.cache/reference/tests/drivers/test_sqlite_remote.py','--mysql-server','root:p06-disposable@127.0.0.1:53316','--postgresql-server','postgres:p06-disposable@127.0.0.1:55436','--junitxml=process/validation/evidence/p08/upstream-final.xml'],native),
      ('integration',[python,'process/validation/tools/p07_validate.py'],dict(native,TC_INTEGRATION_OUT=str(ROOT/'process/validation/artifacts/p08-integration'))),
      ('settings',[python,'process/validation/tools/p07_settings.py'],dict(base,TC_P07_BINARY=str(ROOT/binary),TC_SETTINGS_OUT=str(evidence/'settings.json'))),
      ('remote',[python,'process/validation/tools/p07_remote.py'],dict(native,TC_REMOTE_OUT=str(ROOT/'process/validation/artifacts/p08-remote'))),
      ('preparation',[python,'process/validation/tools/p05_validate.py','--binary',binary,'--output','process/validation/artifacts/p08-preparation'],base),
      ('engines',[python,'process/validation/tools/p06_validate.py'],dict(base,TC_P06_BINARY=binary)),
      ('migrations',[python,'process/validation/tools/p06_migrations.py'],dict(base,TC_P06_BINARY=binary)),
      ('rollback',[python,'process/validation/tools/p08_rollback.py'],base),
      ('geography',[python,'process/validation/tools/p08_geography.py'],dict(base,TC_GEOGRAPHY_OUT='process/validation/artifacts/p08-geography-final')),
      ('geographic-metadata',[python,'process/validation/tools/p08_geographic_metadata.py'],base),
      ('bigtiff',[python,'process/validation/tools/p08_bigtiff.py'],base),
      ('resources',[python,'process/validation/tools/p07_resources.py'],dict(native,TC_RESOURCE_OUT=str(ROOT/'process/validation/artifacts/p08-resources-final'))),
      ('failure',[python,'process/validation/tools/p02_failure.py','--binary',binary,'--out','process/validation/artifacts/p08-failure-final'],base),
      ('soak',[python,'process/validation/tools/p08_soak.py','--out','process/validation/artifacts/p08-soak-final'],base),
      ('shutdown',[python,'process/validation/tools/p08_shutdown.py'],base),
      ('container',[python,'process/validation/tools/p03_container.py','--bundle',bundle,'--out','process/validation/artifacts/p08-container-final','--tag','terracotta-p08-final:local'],base),
      ('lambda',[python,'process/validation/tools/p03_lambda_runtime.py','--bundle',bundle,'--lambda-zip','process/validation/artifacts/p08-final-lambda/terracotta-lambda.zip','--out','process/validation/artifacts/p08-lambda-final'],base),
    ]
    for name,command,env in jobs:
        if any(r['name']==name and r['accepted'] for r in rows):continue
        log=evidence/(name+'-final.log');started=time.monotonic()
        with log.open('w') as stream:result=subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=stream)
        accepted=result.returncode==0;classification='passed' if accepted else 'failed'
        if name=='upstream' and result.returncode==1:
            cases=list(ET.parse(evidence/'upstream-final.xml').iter('testcase'))
            failed=[c.attrib['name'] for c in cases if c.find('failure') is not None]
            skipped=[c for c in cases if c.find('skipped') is not None]
            errors=[c for c in cases if c.find('error') is not None]
            accepted=sorted(failed)==['test_logging_visible[debug]','test_logging_visible[info]'] and len(cases)==528 and not skipped and not errors
            classification='526 passed, 2 frozen-oracle ANSI failures (RW-036); 6 boto-mock cases excluded explicitly (RW-037)' if accepted else 'unclassified upstream failure'
        rows.append(dict(name=name,command=command,exit_code=result.returncode,accepted=accepted,classification=classification,
            seconds=time.monotonic()-started,completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),log=str(log.relative_to(ROOT))))
        write_json(manifest,dict(checks=rows,complete=False))
        print(name,classification,flush=True)
        if not accepted:raise SystemExit(result.returncode or 1)
    write_json(manifest,dict(checks=rows,complete=True))


if __name__=='__main__':main()
