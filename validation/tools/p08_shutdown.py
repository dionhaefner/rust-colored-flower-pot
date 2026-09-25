"""Repeated cold native process exits in the official Lambda runtime image."""
import argparse
import base64
import io
import json
import shutil
import sqlite3
import stat
import subprocess
import zipfile

import numpy as np
from PIL import Image
from common import ROOT, sha256, write_json
from p03_lambda_runtime import DEFAULT_IMAGE


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--count',type=int,default=200)
    p.add_argument('--out',default='validation/artifacts/p08-shutdown')
    args=p.parse_args();out=ROOT/args.out;out.mkdir(parents=True,exist_ok=False)
    package=out/'package';package.mkdir();fixture=out/'fixture';fixture.mkdir();results=out/'results';results.mkdir()
    archive=ROOT/'validation/artifacts/p08-final-lambda/terracotta-lambda.zip'
    with zipfile.ZipFile(archive) as src:
        src.extractall(package)
        for info in src.infolist():
            if not info.is_dir():(package/info.filename).chmod(stat.S_IMODE(info.external_attr>>16))
    shutil.copy2(ROOT/'.cache/corpus/subsets/lagoon-red.tif',fixture/'raster.tif')
    source=ROOT/'validation/artifacts/capture-reproduction/reference.sqlite'
    with sqlite3.connect(source) as db,sqlite3.connect(fixture/'catalog.sqlite') as copy:db.backup(copy)
    with sqlite3.connect(fixture/'catalog.sqlite') as db:db.execute("UPDATE datasets SET path='/data/raster.tif' WHERE scene='lagoon' AND band='red'")
    write_json(fixture/'event.json',dict(version='2.0',rawPath='/singleband/lagoon/red/preview.png',rawQueryString='stretch_range=%5B0%2C3000%5D',requestContext={'http':{'method':'GET'}}))
    # Each iteration is a fresh native process, with a separate result and exit
    # code. Do not hide crashes by retrying until one image succeeds.
    script='i=0; while [ "$i" -lt "$1" ]; do /opt/terracotta/bin/terracotta lambda-event -d /data/catalog.sqlite --event /data/event.json > /results/$i.json 2> /results/$i.stderr; code=$?; printf "%s %s\\n" "$i" "$code" >> /results/exits.txt; i=$((i+1)); done'
    cmd=['docker','run','--rm','--network','none','--entrypoint','/bin/sh','-v',str(package/'runtime')+':/opt/terracotta:ro','-v',str(fixture)+':/data:ro','-v',str(results)+':/results',DEFAULT_IMAGE,'-c',script,'p08-shutdown',str(args.count)]
    process=subprocess.run(cmd,capture_output=True,text=True,check=True)
    (out/'container.stderr').write_text(process.stderr)
    golden=np.asarray(Image.open(ROOT/'validation/reference/capture/images/lagoon-red-preview.png').convert('RGBA'))
    rows=[]
    for line in (results/'exits.txt').read_text().splitlines():
        index,code=map(int,line.split());exact=False
        if code==0:
            response=json.loads((results/f'{index}.json').read_text())
            pixels=np.asarray(Image.open(io.BytesIO(base64.b64decode(response['body']))).convert('RGBA'))
            exact=response['statusCode']==200 and np.array_equal(pixels,golden)
        rows.append(dict(iteration=index,exit_code=code,decoded_rgba_exact=bool(exact)))
    report=dict(passed=len(rows)==args.count and all(r['exit_code']==0 and r['decoded_rgba_exact'] for r in rows),
        iterations=args.count,runtime_image=DEFAULT_IMAGE,zip_sha256=sha256(archive),results=rows,
        purpose='Regression for RW-039; cold native processes inside one clean runtime container. Native worker join and thread-local cleanup must finish before every process exits.')
    write_json(out/'report.json',report);print(json.dumps(dict(passed=report['passed'],iterations=args.count)))
    assert report['passed']


if __name__=='__main__':main()
