"""Retain compact final reports and identify the exact qualified source/build."""
import json
import platform
import shutil
from pathlib import Path
from common import ROOT, COMMIT, sha256, write_json


def main():
    out=ROOT/'validation/evidence/p08'
    reports={
      'http':'p08-http-final','adapters':'p08-adapters-final','integration':'p08-integration',
      'remote':'p08-remote','preparation':'p08-preparation','rollback':'p08-rollback',
      'geography':'p08-geography-final','bigtiff':'p08-bigtiff','resources':'p08-resources-final',
      'failure':'p08-failure-final','soak':'p08-soak-final','shutdown':'p08-shutdown',
      'container':'p08-container-final','lambda-runtime':'p08-lambda-final',
      'benchmark':'p08-benchmark','engines':'p06','migrations':'p06-migrations',
      'install':'p08-final-install-smoke',
      'geographic-metadata':'p08-geographic-metadata',
    }
    for name,directory in reports.items():shutil.copy2(ROOT/'validation/artifacts'/directory/'report.json',out/(name+'.json'))
    images=out/'images';images.mkdir(exist_ok=True)
    for directory,pattern in [('p08-geography-final','images/*.png'),('p08-geography-final','side-by-side.png'),('p08-bigtiff','*.png'),('p08-rollback','*.png')]:
        destination=images/directory;destination.mkdir(exist_ok=True)
        for p in (ROOT/'validation/artifacts'/directory).glob(pattern):shutil.copy2(p,destination/p.name)
    source=[]
    for directory in ['src','python','packaging','tests','.github','validation/tools']:
        for p in sorted((ROOT/directory).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':source.append(p)
    source.extend(ROOT/p for p in ['Cargo.toml','Cargo.lock','rust-toolchain.toml'])
    files=lambda paths:[dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,sha256=sha256(p)) for p in paths]
    artifacts=[ROOT/'validation/artifacts/p08-final/bin/terracotta',ROOT/'validation/artifacts/p08-final/lib/libterracotta_core.so',ROOT/'validation/artifacts/p08-final-lambda/terracotta-lambda.zip',*sorted((ROOT/'validation/artifacts/p08-final-wheels').glob('*.whl'))]
    runtime=[p for folder in ['lib','share'] for p in sorted((ROOT/'validation/artifacts/p08-final'/folder).rglob('*')) if p.is_file()]
    write_json(out/'run-manifest.json',dict(phase='P08',reference_commit=COMMIT,platform=platform.platform(),
        reference_lock=files([ROOT/'validation/reference/requirements.lock'])[0],source=files(source),artifacts=files(artifacts),runtime=files(runtime),
        commands='validation/evidence/p08/qualification.json',
        git_state='Initially wholly untracked; no commit, push, publishing or deployment performed.',
        release_state='Local work qualified; full release blocked on actual AWS, deferred platforms and external basemap workflow.'))
    print('Retained',len(reports),'final reports and',len(source),'source fingerprints')


if __name__=='__main__':main()
