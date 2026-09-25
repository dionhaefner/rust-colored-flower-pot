"""Verify the pinned P08 geography corpus; downloads require --download."""
import argparse
import json
import urllib.request
from common import ROOT, sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--download',action='store_true');args=parser.parse_args()
    assets=json.loads((ROOT/'validation/evidence/p08/geography-assets.json').read_text())
    for asset in assets:
        path=ROOT/asset['path']
        if not path.exists():
            if not args.download:raise FileNotFoundError(f'{path}: explicitly run --download to acquire pinned source')
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.download')
            try:
                with urllib.request.urlopen(asset['url'],timeout=120) as response,temporary.open('wb') as output:
                    while chunk:=response.read(1024*1024):output.write(chunk)
                assert temporary.stat().st_size==asset['bytes'] and sha256(temporary)==asset['sha256'],asset['tile']
                temporary.replace(path)
            finally:temporary.unlink(missing_ok=True)
        assert path.stat().st_size==asset['bytes'] and sha256(path)==asset['sha256'],asset['tile']
        print('verified',asset['tile'],asset['sha256'])


if __name__=='__main__':main()
