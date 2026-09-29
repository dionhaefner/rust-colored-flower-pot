"""Physical >4 GiB BigTIFF from every real WorldCover pixel, local and range I/O.

The sole value transformation is exact uint8 -> uint32 widening to exercise
64-bit offsets. No repeated image, sparse hole or invented pixels; all blocks
are written and compared. This is derived TIFF encoding, not publisher encoding.
"""
import io
import json
import math
import multiprocessing
from pathlib import Path
import time
from urllib.parse import urlencode

import mercantile
import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import Resampling
import tifffile
import terracotta as tc
from terracotta.server import create_app
from common import COMMIT, ROOT, sha256, write_json
from p08_runtime import Service, fetch
from range_server import RangeServer


class OffsetServer(RangeServer):
    def __init__(self, root):
        self.offsets=[]
        super().__init__(root, 0)

    def _serve(self, handler, head):
        value=handler.headers.get('Range','')
        if value.startswith('bytes='):
            self.offsets.append(int(value.split('=')[1].split('-')[0]))
        return super()._serve(handler, head)


def range_worker(root, pipe):
    server=OffsetServer(root)
    pipe.send(server.port)
    try:
        while True:
            command=pipe.recv()
            if command=='close':break
            pipe.send(dict(traffic=server.snapshot(),max_offset=max(server.offsets,default=0)))
    finally:server.close()


class ProcessRangeServer:
    def __init__(self,root):
        ctx=multiprocessing.get_context('spawn');self.pipe,child=ctx.Pipe()
        self.process=ctx.Process(target=range_worker,args=(root,child));self.process.start()
        assert self.pipe.poll(30),'range server startup'
        self.port=self.pipe.recv()
    def snapshot(self):
        self.pipe.send('snapshot');assert self.pipe.poll(30);return self.pipe.recv()
    def close(self):
        self.pipe.send('close');self.process.join(10)
        if self.process.is_alive():self.process.terminate();self.process.join()


def main():
    out=ROOT/'process/validation/artifacts/p08-bigtiff';out.mkdir(parents=True,exist_ok=True)
    path=out/'worldcover-u32.tif';source=ROOT/'.cache/corpus/worldcover.tif'
    expected=json.loads((ROOT/'process/validation/reference/assets.json').read_text())['assets'][-1]['sha256']
    assert sha256(source)==expected
    started=time.monotonic()
    with rasterio.Env(GDAL_CACHEMAX=64*1024**2), rasterio.open(source) as src:
        profile=src.profile.copy();profile.update(dtype='uint32',compress='none',BIGTIFF='YES',tiled=True,blockxsize=512,blockysize=512)
        if not path.exists():
            with rasterio.open(path,'w',**profile) as dst:
                for _,window in dst.block_windows(1): dst.write(src.read(1,window=window).astype('uint32'),1,window=window)
                dst.build_overviews([2,4,8,16,32,64],Resampling.nearest)
        with rasterio.open(path) as dst:
            blocks=0
            for _,window in dst.block_windows(1):
                assert np.array_equal(src.read(1,window=window),dst.read(1,window=window))
                assert np.array_equal(src.read_masks(1,window=window),dst.read_masks(1,window=window))
                blocks+=1
    stat=path.stat();assert stat.st_size>2**32 and stat.st_blocks*512>2**32
    with tifffile.TiffFile(path) as tif:
        assert tif.is_bigtiff
        offsets=list(tif.pages[0].dataoffsets)
    ncols=math.ceil(profile['width']/512)
    chosen=None
    with rasterio.open(source) as src:
        for index,offset in enumerate(offsets):
            if offset<=2**32: continue
            row,col=divmod(index,ncols)
            window=rasterio.windows.Window(col*512,row*512,min(512,src.width-col*512),min(512,src.height-row*512))
            if len(np.unique(src.read(1,window=window)))>=3:
                chosen=dict(index=index,offset=offset,window=list(window.flatten()))
                lon,lat=src.xy(row*512+window.height//2,col*512+window.width//2)
                break
    assert chosen is not None
    tc.update_settings(USE_MULTIPROCESSING=False,RASTER_CACHE_SIZE=0)
    if (out/'catalog.sqlite').exists():(out/'catalog.sqlite').unlink()
    driver=tc.get_driver(out/'catalog.sqlite');driver.create(['scene','band'])
    metadata=driver.compute_metadata(str(source),max_shape=(512,512))
    remote=ProcessRangeServer(out);results=[]
    try:
        for scene,raster in [('local',str(path)),('remote',f'http://127.0.0.1:{remote.port}/{path.name}'),('parent',str(source))]:
            driver.insert((scene,'classes'),raster,metadata=metadata)
        tc.update_settings(DRIVER_PATH=str(out/'catalog.sqlite'));client=create_app().test_client()
        palette={str(k):v for k,v in [(10,[0,100,0]),(20,[255,187,34]),(30,[255,255,76]),(40,[240,150,255]),(50,[250,0,0]),(60,[180,180,180]),(70,[240,240,240]),(80,[0,100,200]),(90,[0,150,160]),(95,[0,207,117]),(100,[250,230,160])]}
        options=urlencode(dict(colormap='explicit',explicit_color_map=json.dumps(palette)))
        with Service(out/'catalog.sqlite',out/'server',extra={'GDAL_CACHEMAX':'64'}) as service:
            golden={}
            for scene in ['parent','local','remote']:
                for zoom in [None,10,14]:
                    tile=mercantile.tile(lon,lat,zoom) if zoom is not None else None
                    suffix=f'{zoom}/{tile.x}/{tile.y}.png' if tile else 'preview.png'
                    url=f'/singleband/{scene}/classes/{suffix}?{options}'
                    ref=client.get(url);status,body=fetch(service.base,url)
                    a=np.asarray(Image.open(io.BytesIO(ref.data)).convert('RGBA'));b=np.asarray(Image.open(io.BytesIO(body)).convert('RGBA'))
                    exact=ref.status_code==status==200 and np.array_equal(a,b)
                    # Overview kernels differ from the publisher; at base resolution
                    # category identity must also match the original uint8 file.
                    if scene=='parent':golden[zoom]=a
                    parent_equal=np.array_equal(golden[zoom],a)
                    if zoom==14:assert parent_equal
                    (out/f'{scene}-{zoom}-python.png').write_bytes(ref.data);(out/f'{scene}-{zoom}-rust.png').write_bytes(body)
                    results.append(dict(scene=scene,zoom=zoom,url=url,passed=exact,parent_equal=parent_equal))
            resources=service.resources();assert resources['peak_rss']<512*1024**2,resources
        traffic=remote.snapshot();assert traffic['max_offset']>2**32,traffic
        report=dict(passed=all(r['passed'] for r in results),reference_commit=COMMIT,source_sha256=expected,
            derivative_sha256=sha256(path),file_bytes=stat.st_size,allocated_bytes=stat.st_blocks*512,
            shape=[profile['height'],profile['width']],blocks_verified=blocks,max_base_offset=max(offsets),
            selected_block=chosen,max_http_range_start=traffic['max_offset'],range_traffic=traffic['traffic'],
            resources=resources,seconds=time.monotonic()-started,results=results,
            recipe='Full WorldCover, all samples/masks compared; exact uint8 to uint32 widening, uncompressed tiled BigTIFF 512x512, nearest overviews 2,4,8,16,32,64. Derived encoding, no publisher-native BigTIFF claim.')
        write_json(out/'report.json',report);print(json.dumps(report,indent=2));assert report['passed']
    finally:remote.close()


if __name__=='__main__':main()
