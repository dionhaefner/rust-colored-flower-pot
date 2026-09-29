"""Derive equal-input Web Mercator tiled GeoTIFFs for Python/Rust map tests."""
import hashlib
import json
from pathlib import Path
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject
from rasterio.shutil import copy as copy_raster

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / '.cache/corpus'
OUTPUT = ROOT / '.cache/map10-mercator'
OUTPUT.mkdir(parents=True, exist_ok=True)
COG_OUTPUT = ROOT / '.cache/map10-mercator-cog'
COG_OUTPUT.mkdir(parents=True, exist_ok=True)
manifest = []
for name in ('s2-red', 's2-green', 's2-blue'):
    src_path = SOURCE / f'{name}.tif'
    dst_path = OUTPUT / f'{name}.tif'
    if not dst_path.exists():
        with rasterio.open(src_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs, 'EPSG:3857', src.width, src.height, *src.bounds)
            profile = src.profile.copy()
            profile.update(driver='GTiff', crs='EPSG:3857', transform=transform,
                           width=width, height=height, tiled=True,
                           blockxsize=512, blockysize=512, compress='deflate',
                           predictor=2, nodata=0, BIGTIFF='IF_SAFER')
            tmp_path = dst_path.with_suffix('.part.tif')
            with rasterio.open(tmp_path, 'w', **profile) as dst:
                reproject(rasterio.band(src, 1), rasterio.band(dst, 1),
                          src_transform=src.transform, src_crs=src.crs,
                          dst_transform=transform, dst_crs='EPSG:3857',
                          src_nodata=0, dst_nodata=0, resampling=Resampling.bilinear,
                          num_threads=3)
                dst.build_overviews([2, 4, 8, 16], Resampling.average)
                dst.update_tags(ns='rio_overview', resampling='average')
            tmp_path.replace(dst_path)
    cog_path = COG_OUTPUT / f'{name}.tif'
    if not cog_path.exists():
        tmp_cog = cog_path.with_suffix('.part.tif')
        copy_raster(dst_path, tmp_cog, driver='COG', compress='DEFLATE', blocksize=512,
                    overview_resampling='average', num_threads=3)
        tmp_cog.replace(cog_path)
    with rasterio.open(cog_path) as cog:
        assert cog.tags(ns='IMAGE_STRUCTURE').get('LAYOUT') == 'COG'
        assert cog.crs.to_epsg() == 3857 and cog.overviews(1) == [2, 4, 8, 16]
    with rasterio.open(dst_path) as dst:
        assert dst.crs.to_epsg() == 3857 and dst.overviews(1) == [2, 4, 8, 16]
        record = dict(name=name, source=str(src_path.relative_to(ROOT)),
                      output=str(dst_path.relative_to(ROOT)),
                      size=dst_path.stat().st_size, shape=dst.shape,
                      source_sha256=hashlib.sha256(src_path.read_bytes()).hexdigest(),
                      output_sha256=hashlib.sha256(dst_path.read_bytes()).hexdigest(),
                      cog=str(cog_path.relative_to(ROOT)),
                      cog_size=cog_path.stat().st_size,
                      cog_sha256=hashlib.sha256(cog_path.read_bytes()).hexdigest())
        manifest.append(record)
        print(name, record['shape'], record['cog_size'], record['cog_sha256'], flush=True)
(OUTPUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
