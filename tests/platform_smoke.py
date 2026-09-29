"""Portable installed-wheel/native smoke using checksum-pinned Sentinel pixels."""
import argparse, base64, hashlib, io, json, os, platform, shutil, socket, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--native-only', action='store_true')
    a = p.parse_args(); binary = a.binary.resolve() if a.binary else None; out = a.out.resolve()
    if a.native_only and binary is None:
        p.error('--native-only requires --binary')
    if binary is not None and os.name == 'nt' and binary.suffix != '.exe':
        binary = binary.with_suffix('.exe')
    out.mkdir(parents=True, exist_ok=True)
    # Use the pinned real raster pixels for this smoke check.
    raster = ROOT/'.cache/platform-smoke/lagoon-red.tif'
    fixture = json.loads((ROOT/'tests/fixtures/platform-smoke.json').read_text())
    if not raster.exists():
        asset = fixture['source']
        parent = out/'sentinel-red.tif'
        urllib.request.urlretrieve(asset['url'], parent)
        assert parent.stat().st_size == asset['bytes']
        assert hashlib.sha256(parent.read_bytes()).hexdigest() == asset['sha256']
        raster.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(parent) as src:
            window = Window(*fixture['window'])
            profile = src.profile.copy()
            profile.update(width=2048, height=2048, transform=src.window_transform(window), tiled=False, compress='deflate')
            profile.pop('blockxsize', None); profile.pop('blockysize', None)
            with rasterio.open(raster, 'w', **profile) as dst:
                dst.write(src.read(1, window=window), 1)
    with rasterio.open(raster) as src:
        expected = fixture['subset']
        assert (src.width, src.height) == (expected['width'], expected['height'])
        assert str(src.crs) == expected['crs']
        assert tuple(src.transform)[:6] == tuple(expected['transform'])
        assert hashlib.sha256(src.read(1).tobytes()).hexdigest() == expected['pixel_sha256']
    database = out/'platform.sqlite'
    assert not database.exists(), 'choose a fresh output directory'
    checks = []
    golden_path = ROOT/'tests/fixtures/lagoon-red-preview.png'
    assert hashlib.sha256(golden_path.read_bytes()).hexdigest() == fixture['preview_png_sha256']
    golden = np.asarray(Image.open(golden_path).convert('RGBA'))
    if binary is not None:
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
        assert response['statusCode']==200 and np.array_equal(np.asarray(Image.open(io.BytesIO(png)).convert('RGBA')),golden)
        checks.append('native-create-insert-lambda-render-exact')
    else:
        import rcfp as rust
        assert Path(rust.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), rust.__file__
        store = rust.DataStore(database)
        store.create(['scene', 'band'])
        store.insert(['lagoon', 'red'], raster)
        status, content_type, png, _ = rust.request(database, 'GET',
            '/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D')
        (out/'wheel.png').write_bytes(png)
        assert status == 200 and content_type == 'image/png'
        assert np.array_equal(np.asarray(Image.open(io.BytesIO(png)).convert('RGBA')), golden)
        checks.append('installed-wheel-create-insert-render-exact')
        # Exercise a compact public HTTP contract through the installed native
        # extension. The same real raster is registered under three bands so
        # RGB and compute reach the actual GDAL and rendering code paths.
        for band in ('green', 'blue'):
            store.insert(['lagoon', band], raster)
        def get(path, headers=None, method='GET'):
            return rust.request_full(database, method, path, headers or {})
        status, kind, body, _, _ = get('/keys')
        assert status == 200 and kind == 'application/json'
        assert [row['key'] for row in json.loads(body)['keys']] == ['scene', 'band']
        status, kind, body, _, _ = get('/datasets?scene=lagoon')
        assert status == 200 and kind == 'application/json'
        assert {row['band'] for row in json.loads(body)['datasets']} == {'red', 'green', 'blue'}
        status, kind, body, _, _ = get('/metadata/lagoon/red')
        assert status == 200 and kind == 'application/json'
        assert json.loads(body)['keys'] == {'scene': 'lagoon', 'band': 'red'}
        checks.append('installed-wheel-discovery-metadata')
        for name, path in (
            ('rgb', '/rgb/lagoon/preview.png?r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D'),
            ('compute', '/compute/lagoon/preview.png?expression=v1&v1=red&stretch_range=%5B0%2C3000%5D&colormap=viridis'),
        ):
            status, kind, body, _, _ = get(path)
            assert status == 200 and kind == 'image/png', (name, status, body[:150])
            assert np.asarray(Image.open(io.BytesIO(body)).convert('RGBA')).shape == golden.shape
            checks.append('installed-wheel-' + name + '-render')
        status, kind, body, _, _ = get('/singleband/lagoon/red/preview.png?tile_size=bad')
        assert status == 400 and kind == 'application/json' and 'message' in json.loads(body)
        status, _, _, _, headers = get('/keys', {'origin': 'https://example.com'}, 'OPTIONS')
        assert status == 200 and headers.get('Access-Control-Allow-Origin') == 'https://example.com'
        checks.append('installed-wheel-invalid-query-cors')
    if not a.native_only:
        import rcfp
        from rcfp.wsgi import Application
        assert Path(rcfp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), rcfp.__file__
        status=[]
        body=b''.join(Application(str(database))({'REQUEST_METHOD':'GET','PATH_INFO':'/singleband/lagoon/red/preview.png',
            'QUERY_STRING':'stretch_range=%5B0%2C3000%5D','wsgi.input':io.BytesIO()},lambda code,headers:status.append(code)))
        assert status==['200 OK'] and np.array_equal(np.asarray(Image.open(io.BytesIO(body)).convert('RGBA')),golden)
        subprocess.run([sys.executable,'-m','rcfp.cli','--help'],check=True)
        checks.extend(['installed-rcfp-wsgi-exact','installed-rcfp-cli'])
        if binary is None:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            server = subprocess.Popen([sys.executable, '-m', 'rcfp.cli', 'serve',
                                       '-d', str(database), '--bind', f'127.0.0.1:{port}'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/keys', timeout=1) as response:
                            assert response.status == 200
                            assert [row['key'] for row in json.load(response)['keys']] == ['scene', 'band']
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if server.poll() is not None or time.monotonic() >= deadline:
                            raise AssertionError('installed CLI server failed to start')
                        time.sleep(0.1)
                url = f'http://127.0.0.1:{port}/singleband/lagoon/red/preview.png?stretch_range=%5B0%2C3000%5D'
                with urllib.request.urlopen(url, timeout=10) as response:
                    assert response.status == 200 and response.headers.get_content_type() == 'image/png'
                    served = response.read()
                assert np.array_equal(np.asarray(Image.open(io.BytesIO(served)).convert('RGBA')), golden)
                checks.append('installed-rcfp-cli-http-server-exact')
            finally:
                server.terminate()
                try:
                    server.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill(); server.communicate()
    report=dict(passed=True,checks=checks,platform=platform.platform(),machine=platform.machine(),python=sys.version,
        rasterio=rasterio.__version__,gdal=rasterio.__gdal_version__,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest() if binary else None)
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
