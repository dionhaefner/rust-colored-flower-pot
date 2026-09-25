"""Verify and execute the canonical local candidate archives before final sealing."""
import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import tarfile
import zipfile
from common import ROOT, write_json, sha256


def main():
    candidate=ROOT/'validation/artifacts/p08-candidate-preflight'
    out=ROOT/'validation/artifacts/p08-archive-smoke';out.mkdir(exist_ok=False)
    manifest=json.loads((candidate/'manifest.json').read_text())
    for row in manifest['artifacts']:
        path=candidate/row['file'];assert path.stat().st_size==row['bytes'] and sha256(path)==row['sha256']
    with tarfile.open(candidate/'terracotta-0.1.0-linux-x86_64.tar.gz') as archive:
        archive.extractall(out,filter='data')
    bundle=out/'terracotta-0.1.0';binary=bundle/'bin/terracotta'
    wheel=next(candidate.glob('*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        record=next(n for n in archive.namelist() if n.endswith('.dist-info/RECORD'))
        members={n for n in archive.namelist() if not n.endswith('/')}
        entries=list(csv.reader(io.StringIO(archive.read(record).decode())))
        assert members=={r[0] for r in entries}
        for name,digest,size in entries:
            if name==record:continue
            body=archive.read(name);assert len(body)==int(size)
            assert digest=='sha256='+base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b'=').decode()
    env=dict(os.environ);env.pop('PYTHONPATH',None)
    python=ROOT/'validation/artifacts/p08-final-install/bin/python'
    subprocess.run([str(python),'-m','pip','install','--no-deps','--no-index','--force-reinstall',str(wheel)],env=env,check=True)
    subprocess.run([str(python),'validation/tools/p07_platform_smoke.py','--binary',str(binary),'--out',str(out/'installed')],env=env,check=True)
    subprocess.run([str(ROOT/'.venv-reference/bin/python'),'validation/tools/p03_lambda_runtime.py','--bundle',str(bundle),
        '--lambda-zip',str(candidate/'terracotta-0.1.0-lambda-x86_64.zip'),'--out',str(out/'lambda')],check=True)
    write_json(ROOT/'validation/evidence/p08/archive-smoke.json',dict(passed=True,
        scope='Canonical native tarball extracted; canonical wheel RECORD checked and installed offline in the previously fresh final environment; installed native/Python/WSGI smoke and official AL2023 canonical ZIP smoke passed.',
        artifacts=manifest['artifacts'],installed=json.loads((out/'installed/report.json').read_text()),
        lambda_runtime=json.loads((out/'lambda/report.json').read_text())))


if __name__=='__main__':main()
