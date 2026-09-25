"""Portable installed-wheel/native smoke using checksum-pinned Sentinel pixels."""
import argparse, base64, hashlib, io, json, os, platform, shutil, subprocess, sys, urllib.request
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--native-only', action='store_true')
    a = p.parse_args(); binary = a.binary.resolve(); out = a.out.resolve()
    if os.name == 'nt' and binary.suffix != '.exe':
        binary = binary.with_suffix('.exe')
    out.mkdir(parents=True, exist_ok=True)
    # Never generate substitute pixels for platform qualification.
    raster = ROOT/'.cache/corpus/subsets/lagoon-red.tif'
    if not raster.exists():
        asset = json.loads((ROOT/'validation/reference/assets.json').read_text())['assets'][0]
        parent = out/'sentinel-red.tif'
        urllib.request.urlretrieve(asset['url'], parent)
        assert hashlib.sha256(parent.read_bytes()).hexdigest() == asset['sha256']
        raster.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(parent) as src:
            window = Window(5500, 5250, 2048, 2048)
            profile = src.profile.copy()
            profile.update(width=2048, height=2048, transform=src.window_transform(window), tiled=False, compress='deflate')
            profile.pop('blockxsize', None); profile.pop('blockysize', None)
            with rasterio.open(raster, 'w', **profile) as dst:
                dst.write(src.read(1, window=window), 1)
    database = out/'platform.sqlite'
    assert not database.exists(), 'choose a fresh output directory'
    checks = []
    def operation(request):
        request_file = out/'operation.json'; request_file.write_text(json.dumps(request))
        return json.loads(subprocess.check_output([str(binary), 'data-operation', str(request_file)], text=True))
    operation(dict(operation='create', path=str(database), keys=[dict(key='scene', description=''),dict(key='band',description='')]))
    operation(dict(operation='insert', path=str(database), keys=['lagoon','red'], raster=str(raster)))
    event = out/'event.json'
    event.write_text(json.dumps(dict(version='2.0',rawPath='/singleband/lagoon/red/preview.png',
        rawQueryString='stretch_range=%5B0%2C3000%5D',requestContext={'http':{'method':'GET'}})))
    response = json.loads(subprocess.check_output([str(binary),'lambda-event','-d',str(database),'--event',str(event)], text=True))
    png = base64.b64decode(response['body']); (out/'native.png').write_bytes(png)
    golden = np.asarray(Image.open(ROOT/'validation/reference/capture/images/lagoon-red-preview.png').convert('RGBA'))
    assert response['statusCode']==200 and np.array_equal(np.asarray(Image.open(io.BytesIO(png)).convert('RGBA')),golden)
    checks.append('native-create-insert-lambda-render-exact')
    if not a.native_only:
        import terracotta as tc
        from terracotta_rust.wsgi import Application
        tc.update_settings(USE_MULTIPROCESSING=False)
        driver = tc.get_driver(database)
        assert driver.key_names == ('scene','band')
        result = driver.get_raster_tile(('lagoon','red'), asynchronous=True).result(timeout=30)
        assert result.shape == (256,256) and result.dtype == np.uint16
        status=[]
        body=b''.join(Application(str(database))({'REQUEST_METHOD':'GET','PATH_INFO':'/singleband/lagoon/red/preview.png',
            'QUERY_STRING':'stretch_range=%5B0%2C3000%5D','wsgi.input':io.BytesIO()},lambda s,h:status.append(s)))
        assert status==['200 OK'] and np.array_equal(np.asarray(Image.open(io.BytesIO(body)).convert('RGBA')),golden)
        from terracotta.server import create_app
        tc.update_settings(DRIVER_PATH=str(database))
        assert create_app().test_client().get('/keys').status_code==200
        subprocess.run([sys.executable,'-m','terracotta.scripts.cli','--help'],check=True)
        checks.extend(['installed-python-future-array','wsgi-exact','flask-keys'])
    report=dict(passed=True,checks=checks,platform=platform.platform(),machine=platform.machine(),python=sys.version,
        rasterio=rasterio.__version__,gdal=rasterio.__gdal_version__,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
