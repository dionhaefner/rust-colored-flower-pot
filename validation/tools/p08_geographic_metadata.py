"""Full, sampled and chunked metadata on the pinned P08 geographic derivatives."""
import numpy as np
import rasterio
from shapely.geometry import shape
from terracotta.raster import compute_metadata
from common import ROOT, COMMIT, write_json, sha256
import p05_validate as base


def main():
    base.OUT=ROOT/'validation/artifacts/p08-geographic-metadata'
    base.OUT.mkdir(parents=True,exist_ok=False)
    base.BINARY=ROOT/'validation/artifacts/p08-final/bin/terracotta'
    rows=[]
    for path in sorted((ROOT/'validation/artifacts/p08-geography-final').glob('*.tif')):
        with rasterio.open(path) as src: dtype=src.dtypes[0]
        for mode,options in [('full',{}),('sample',{'max_shape':[173,291]}),('chunk',{'use_chunks':True})]:
            oracle=compute_metadata(str(path),**options)
            native=base.operation(dict(operation='compute_metadata',path=str(path),options=options))
            name=path.stem+'-'+mode
            reduction=None
            if name=='dateline-mosaic-full':
                with rasterio.open(path) as src:
                    values=np.ma.masked_invalid(src.read(1,masked=True)).compressed()
                reduction={field:dict(numpy_float32=float(fn(values)),numpy_float64=float(fn(values,dtype='float64')))
                    for field,fn in [('mean',np.mean),('stdev',np.std)]}
                write_json(base.OUT/'mosaic-reduction.json',dict(samples=int(values.size),sha256=sha256(path),moments=reduction))
            write_json(base.OUT/(name+'-python.json'),base.normalized(oracle))
            write_json(base.OUT/(name+'-rust.json'),native)
            checks=[]
            for field in ['bounds','range','valid_percentage','mean','stdev','percentiles']:
                a=np.asarray(oracle[field]);b=np.asarray(native[field])
                if field=='percentiles':
                    passed=np.array_equal(a.astype('float32'),b.astype('float32'));bound='exact database float32'
                elif field in ['mean','stdev']:
                    # RW-024: NumPy uses float32 reductions on nonchunked float32
                    # inputs; native streaming accumulates in f64. Keep its
                    # existing four-ULP bound, selected by dtype rather than place.
                    atol=4*float(np.spacing(np.float32(abs(a)))) if dtype=='float32' and mode!='chunk' else 1e-10
                    rtol=0 if dtype=='float32' and mode!='chunk' else 2e-12
                    passed=np.allclose(a,b,rtol=rtol,atol=atol);bound=dict(atol=atol,rtol=rtol)
                    if reduction is not None:
                        # RW-042: this 17.28-million-sample input exceeds the
                        # small-crop bound. Prove the precise reduction cause,
                        # and bound only this pinned full-mosaic comparison.
                        atol={'mean':5e-6,'stdev':2.1e-4}[field]
                        passed=(float(a)==reduction[field]['numpy_float32'] and
                            float(b)==float(np.float32(reduction[field]['numpy_float64'])) and abs(float(a-b))<=atol)
                        bound=dict(atol=atol,rtol=0,requires='exact NumPy f32 oracle and rounded NumPy f64 native moments',discovery='RW-042')
                else:passed=np.allclose(a,b,rtol=0,atol=1e-12);bound=dict(atol=1e-12,rtol=0)
                checks.append(dict(field=field,passed=bool(passed),max_difference=float(np.max(np.abs(a-b))),bound=bound))
            a=shape(oracle['convex_hull']);b=shape(native['convex_hull'])
            difference=a.symmetric_difference(b).area/a.union(b).area
            checks.append(dict(field='convex_hull',passed=difference<1e-10,relative_area_difference=difference,bound=1e-10))
            checks.append(dict(field='metadata',passed=oracle['metadata']==native['metadata']))
            rows.append(dict(name=name,path=str(path.relative_to(ROOT)),sha256=sha256(path),dtype=dtype,options=options,checks=checks))
            write_json(base.OUT/'report.json',dict(reference_commit=COMMIT,passed=all(c['passed'] for r in rows for c in r['checks']),results=rows))
            assert all(c['passed'] for c in checks),(name,checks)
    print('PASS',sum(len(r['checks']) for r in rows),'checks across',len(rows),'geographic metadata cases')


if __name__=='__main__':main()
