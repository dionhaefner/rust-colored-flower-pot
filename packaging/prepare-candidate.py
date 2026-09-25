"""Create deterministic local review artifacts; never publish or tag.

Reproducible packaging of identified build outputs does not claim bit-for-bit
reproducible compilation of the native toolchain/dependency stack.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def tarball(output,files,prefix):
    with output.open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as compressed,tarfile.open(fileobj=compressed,mode='w') as archive:
        for name,path in sorted(files):
            info=archive.gettarinfo(str(path),arcname=prefix+'/'+name)
            info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
            if info.isfile():
                with path.open('rb') as stream:archive.addfile(info,stream)
            else:archive.addfile(info)


def canonical_zip(source,destination):
    with zipfile.ZipFile(source) as src,zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as dst:
        for old in sorted(src.infolist(),key=lambda x:x.filename):
            if '__pycache__' in old.filename or old.filename.endswith('.pyc'):continue
            info=zipfile.ZipInfo(old.filename,date_time=(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=old.external_attr;info.compress_type=zipfile.ZIP_DEFLATED
            dst.writestr(info,src.read(old.filename),compresslevel=9)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    bundle=ROOT/'validation/artifacts/p08-final'
    tarball(out/'terracotta-0.1.0-linux-x86_64.tar.gz',[(str(p.relative_to(bundle)),p) for p in bundle.rglob('*') if p.is_file() or p.is_symlink()], 'terracotta-0.1.0')
    canonical_zip(ROOT/'validation/artifacts/p08-final-lambda/terracotta-lambda.zip',out/'terracotta-0.1.0-lambda-x86_64.zip')
    wheel=next((ROOT/'validation/artifacts/p08-final-wheels').glob('*.whl'))
    # RECORD hashes concern member bytes, not ZIP metadata, so normalization
    # preserves the wheel's integrity records.
    canonical_zip(wheel,out/wheel.name)
    roots=['src','python','tests','packaging','.github','execution','validation/tools','validation/reference','validation/evidence']
    files=[]
    for name in roots:
        files.extend((str(p.relative_to(ROOT)),p) for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    for name in ['Cargo.toml','Cargo.lock','rust-toolchain.toml','AGENTS.md','README.md','REWRITE.md','REWRITE_LOG.md','THIRD_PARTY_NOTICES.md','LICENSE','.gitignore','.dockerignore']:
        files.append((name,ROOT/name))
    # Exclude the release's own artifact manifest to avoid circular content hashes.
    files=[(n,p) for n,p in files if n not in ['validation/evidence/p08/release-manifest.json','validation/evidence/p08/reproducibility.json']]
    tarball(out/'terracotta-0.1.0-source-and-evidence.tar.gz',files,'terracotta-0.1.0')
    manifest=dict(implementation='0.1.0',protocol='0.10.1',database='0.10.1',
        status='local review candidate; external release gates remain open',
        reproducibility='Deterministic archive metadata/member ordering over identical inputs; native compilation is identified by source/toolchain/library hashes, not claimed bit-reproducible.',
        artifacts=[dict(file=p.name,bytes=p.stat().st_size,sha256=digest(p)) for p in sorted(out.iterdir())])
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(f'{r["sha256"]}  {r["file"]}\n' for r in manifest['artifacts']))
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
