"""Compare capture contracts, decoded pixels and typed arrays. No pixel tolerance."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from common import read_json, write_json


def structured(left, right, path='$'):
    errors=[]
    if type(left) is not type(right): return [f'{path}: type {type(left).__name__} != {type(right).__name__}']
    if isinstance(left,dict):
        # Preserve object key order too: identity-bearing ordered maps must not be normalized away.
        if list(left)!=list(right): errors.append(f'{path}: keys/order differ')
        for key in left.keys() & right.keys(): errors.extend(structured(left[key],right[key],path+'.'+key))
    elif isinstance(left,list):
        if len(left)!=len(right): errors.append(f'{path}: list length differs')
        for i,(a,b) in enumerate(zip(left,right)): errors.extend(structured(a,b,f'{path}[{i}]'))
    elif left!=right: errors.append(f'{path}: {left!r} != {right!r}')
    return errors


def compare(left,right):
    left,right=Path(left),Path(right)
    a,b=read_json(left/'capture.json'),read_json(right/'capture.json')
    errors=structured(a['contract'],b['contract'])
    for kind in ('images','arrays'):
        if a[kind]!=b[kind]: errors.append(f'{kind}: artifact membership/order differs')
    for name in a['images']:
        if name not in b['images']: continue
        try:
            with Image.open(left/name) as x,Image.open(right/name) as y:
                xa,ya=np.asarray(x.convert('RGBA')),np.asarray(y.convert('RGBA'))
                if xa.shape!=ya.shape: errors.append(f'{name}: dimensions differ'); continue
                if not np.array_equal(xa[:,:,3],ya[:,:,3]): errors.append(f'{name}: alpha/mask differs')
                visible=(xa[:,:,3]>0)|(ya[:,:,3]>0)
                diff=np.any(xa[:,:,:3]!=ya[:,:,:3],axis=2)&visible
                if diff.any(): errors.append(f'{name}: {int(diff.sum())} visible pixels differ')
        except (OSError,ValueError) as e: errors.append(f'{name}: unreadable image: {e}')
    for name in a['arrays']:
        if name not in b['arrays']: continue
        try:
            with np.load(left/name,allow_pickle=False) as x,np.load(right/name,allow_pickle=False) as y:
                if x.files!=y.files: errors.append(f'{name}: array members differ'); continue
                for key in x.files:
                    xa,ya=x[key],y[key]
                    if xa.shape!=ya.shape or xa.dtype!=ya.dtype: errors.append(f'{name}/{key}: shape/dtype differs'); continue
                    if not np.array_equal(xa,ya,equal_nan=True): errors.append(f'{name}/{key}: values differ')
        except (OSError,ValueError) as e: errors.append(f'{name}: unreadable array: {e}')
    return {'passed':not errors,'differences':errors,'image_count':len(a['images']),'array_count':len(a['arrays'])}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('reference'); p.add_argument('candidate'); p.add_argument('--report')
    args=p.parse_args(); result=compare(args.reference,args.candidate)
    if args.report: write_json(args.report,result)
    print(json.dumps(result,indent=2)); raise SystemExit(0 if result['passed'] else 1)
