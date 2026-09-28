"""Retain Rust dependency notices alongside the native distribution."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
output=Path(sys.argv[1])
metadata=json.loads(subprocess.check_output([str(root/'validation/tools/cargo.sh'),'metadata','--locked','--offline','--format-version','1'],cwd=root))
manifest=[]
for package in metadata['packages']:
    if package['name']=='rcfp':continue
    source=Path(package['manifest_path']).parent
    candidates=[p for p in source.iterdir() if p.is_file() and p.name.upper().startswith(('LICENSE','COPYING','NOTICE'))]
    if package['name']=='openssl-src':candidates.append(source/'openssl/LICENSE.txt')
    target=output/(package['name']+'-'+package['version'])
    target.mkdir(parents=True,exist_ok=True)
    retained=[]
    for path in candidates:
        if path.is_file():
            name='OPENSSL-LICENSE.txt' if path.parent.name=='openssl' else path.name
            shutil.copyfile(path,target/name);retained.append(name)
    manifest.append(dict(name=package['name'],version=package['version'],license=package['license'],source=package['source'],notices=retained))
(output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
