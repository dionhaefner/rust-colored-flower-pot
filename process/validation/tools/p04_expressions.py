"""Reproduce frozen-reference expression vectors; never uses Rust outputs."""
import json
from pathlib import Path
import sys
import warnings
import numpy as np
from terracotta.expressions import evaluate_expression, EXTRA_CALLABLES
from common import ROOT,COMMIT,write_json
sys.setrecursionlimit(10000)

def capture(operands,expressions):
    cases=[]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for e in expressions:
            c={'expression':e}
            try:
                r=evaluate_expression(e,operands,timeout=10)
                c.update(values=r.filled(0).tolist(),mask=np.ma.getmaskarray(r).tolist(),dtype=str(r.dtype))
            except Exception as exc:c['error']=str(exc)
            cases.append(c)
    return {'reference_commit':COMMIT,'operands':{k:{'values':v.data.tolist(),'mask':np.ma.getmaskarray(v).tolist(),'dtype':str(v.dtype)} for k,v in operands.items()},'cases':cases}

base=ROOT/'process/validation/reference/p04-expressions.json'
existing=json.loads(base.read_text())
operands={k:np.ma.array(v['values'],dtype='float64',mask=v['mask']) for k,v in existing['operands'].items()}
write_json(base,capture(operands,[c['expression'] for c in existing['cases']]))
suites=[]
for dtype in ['uint8','uint16','int16','float32','float64']:
    values=[0,1,2,100,255] if dtype=='uint8' else [0,1,2,3000,10000]
    op={'v1':np.ma.array(values,dtype=dtype),'v2':np.ma.array([2,1,0,3,4],mask=[1,0,0,0,0],dtype=dtype)}
    expressions=['v1','-v1','v1+v2','v1-v2','v1*v2','v1/v2','v1%v2','v1**v2','v1+2','v1-1000','v1*2','v1**2','v1**-1','v1+1.0','v1&v2','~v1','-v1**2','(-v1)**2','where(v1>100, v1, v2)','setmask(v1-v2,nomask)','setmask(v1,~getmask(v2))']
    for name,(_,n) in EXTRA_CALLABLES.items():
        expressions.append(f'{name}('+('v1' if n==1 else 'v1, v2' if n==2 else 'v1, 1, 100')+')')
    suites.append(capture(op,expressions))
op={'v1':np.ma.array([1,2,3],dtype='uint16'),'v2':np.ma.array([.25,3.5,2.75],dtype='float32')}
suites.append(capture(op,['v1+v2','v1*v2','minimum(v1,v2)+v2','maximum(v1,v2)-v1','where(v1<2,v1,v2)*2','v1 + (1/0)','v1 + (1%0)']))
write_json(ROOT/'process/validation/reference/p04-expression-types.json',suites)
