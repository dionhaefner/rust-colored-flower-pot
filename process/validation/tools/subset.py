"""Native-resolution window extraction and independent parent-window validation."""
import argparse
import numpy as np
import rasterio
import tifffile
from rasterio.windows import Window
from common import ROOT, CACHE, read_json, write_json, sha256


def describe(path):
    with rasterio.open(path) as s, tifffile.TiffFile(path) as tif:
        pages=list(tif.pages)
        offsets=[int(x) for p in pages for x in p.dataoffsets]
        return dict(bytes=path.stat().st_size, sha256=sha256(path), width=s.width,height=s.height,
                    bands=s.count,dtypes=s.dtypes,crs=s.crs.to_string(),transform=list(s.transform),
                    bounds=list(s.bounds),scales=s.scales,offsets=s.offsets,nodata=s.nodata,
                    masks=[[f.name for f in fs] for fs in s.mask_flag_enums],
                    compression=s.compression.name if s.compression else None,blocks=s.block_shapes,
                    overviews=s.overviews(1),tiff_variant='BigTIFF' if tif.is_bigtiff else 'classic TIFF',
                    min_block_offset=min(offsets),max_block_offset=max(offsets),
                    blocks_beyond_4GiB=sum(x>=2**32 for x in offsets),
                    image_structure=s.tags(ns='IMAGE_STRUCTURE'))


def subset(verify=False):
    recipes=read_json(ROOT/'process/validation/reference/subsets.json')
    assets=read_json(ROOT/'process/validation/reference/assets.json')['assets']
    output=CACHE/'subsets'; output.mkdir(parents=True,exist_ok=True)
    report={'rasterio':rasterio.__version__,'GDAL':rasterio.__gdal_version__,'parents':{},'subsets':{},'recipe':recipes}
    for a in assets:
        path=CACHE/(a['id']+'.tif')
        if sha256(path)!=a['sha256']: raise ValueError(f'parent checksum mismatch: {path}')
        report['parents'][a['id']]=describe(path)
    for recipe in recipes['subsets']:
        path=output/(recipe['id']+'.tif'); window=Window(*recipe['window'])
        with rasterio.open(CACHE/(recipe['parent']+'.tif')) as src:
            data=src.read(recipe['band'],window=window)
            mask=src.read_masks(recipe['band'],window=window)
            transform=src.window_transform(window)
            if not verify:
                profile=dict(driver='GTiff',width=window.width,height=window.height,count=1,
                             dtype=data.dtype,crs=src.crs,transform=transform,nodata=src.nodata,
                             compress='deflate',tiled=False)
                if recipe.get('internal_mask'): profile['nodata']=None
                temporary=path.with_suffix('.part.tif')
                with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(temporary,'w',**profile) as dst:
                    dst.write(data,1); dst.scales=(src.scales[recipe['band']-1],); dst.offsets=(src.offsets[recipe['band']-1],)
                    if recipe.get('internal_mask') or any(f.name=='per_dataset' for f in src.mask_flag_enums[recipe['band']-1]):
                        dst.write_mask(mask)
                temporary.replace(path)
            with rasterio.open(path) as dst:
                assert dst.crs==src.crs and dst.transform==transform and dst.dtypes[0]==src.dtypes[recipe['band']-1]
                np.testing.assert_array_equal(dst.read(1),data)
                np.testing.assert_array_equal(dst.read_masks(1),mask)
                assert dst.scales[0]==src.scales[recipe['band']-1] and dst.offsets[0]==src.offsets[recipe['band']-1]
            info=describe(path)
            info.update(valid_pixels=int((mask>0).sum()),invalid_pixels=int((mask==0).sum()),
                        valid_zeros=int(((mask>0)&(data==0)).sum()),
                        valid_range=[float(data[mask>0].min()),float(data[mask>0].max())])
            if recipe['parent']=='worldcover': info['categories']=np.unique(data[mask>0]).tolist()
            report['subsets'][recipe['id']]=info
            print(recipe['id'],info['bytes'],info['valid_range'],info['invalid_pixels'],flush=True)
    write_json(ROOT/'process/validation/artifacts/corpus.json',report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--verify',action='store_true')
    subset(p.parse_args().verify)
