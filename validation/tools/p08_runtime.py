"""Disposable service and resource measurements shared by P08 checks."""
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image
import psutil
from common import ROOT

BINARY = Path(os.environ.get('TC_P08_BINARY', ROOT / 'validation/artifacts/p08-final/bin/terracotta')).resolve()
DATABASE = ROOT / 'validation/artifacts/capture-reproduction/reference.sqlite'


def fetch(base, path):
    try:
        response = urllib.request.urlopen(base + path, timeout=120)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def image_hash(body):
    with Image.open(io.BytesIO(body)) as image:
        values = np.asarray(image.convert('RGBA'))
    return hashlib.sha256(str(values.shape).encode() + values.tobytes()).hexdigest()


class Service:
    def __init__(self, database, out, engine='rust', cache=0, cpus=None, extra=None):
        out = Path(out); out.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        env = {k: v for k, v in os.environ.items() if not k.startswith('TC_')}
        env.update(TC_DRIVER_PATH=str(database), TC_DRIVER_PROVIDER='sqlite',
                   TC_USE_MULTIPROCESSING='false', TC_RASTER_WORKERS='3',
                   TC_RASTER_QUEUE_CAPACITY='16', TC_RASTER_TIMEOUT_MS='120000',
                   TC_RASTER_CACHE_BYTES=str(cache), TC_RASTER_CACHE_SIZE=str(cache // 3),
                   GDAL_CACHEMAX='32', GDAL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   OMP_NUM_THREADS='1', TC_RESAMPLING_METHOD='average',
                   TC_REPROJECTION_METHOD='linear')
        env.update(extra or {})
        if engine == 'rust':
            args = [str(BINARY), 'serve', '-d', str(database), '--port', str(port)]
        else:
            env['PYTHONPATH'] = os.pathsep.join(map(str, [ROOT / '.cache/reference', ROOT / 'validation/artifacts/p08-benchmark-tools']))
            args = [str(ROOT / '.venv-reference/bin/python'), '-m', 'gunicorn',
                    '--workers', '3', '--worker-class', 'sync', '--bind', f'127.0.0.1:{port}',
                    '--timeout', '120', 'terracotta.server.app:app']
        if cpus:
            args = ['taskset', '-c', ','.join(map(str, cpus)), *args]
        self.command = args
        self.log = (out / 'server.log').open('w')
        start = time.monotonic()
        self.process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=self.log, stderr=self.log)
        self.root = psutil.Process(self.process.pid)
        self.samples = []
        self.sampling_errors = []
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True); self.thread.start()
        try:
            for _ in range(600):
                if self.process.poll() is not None:
                    raise RuntimeError(f'server exited: {out / "server.log"}')
                try:
                    if fetch(self.base, '/keys')[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(.02)
            else:
                raise TimeoutError('server readiness')
            self.startup_seconds = time.monotonic() - start
        except BaseException:
            self.close(); raise

    def snapshot(self):
        rss = fds = threads = 0
        for process in [self.root, *self.root.children(recursive=True)]:
            try:
                rss += process.memory_info().rss
                fds += process.num_fds()
                threads += process.num_threads()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # /proc/fd can become inaccessible while an exiting process is
                # being reaped. Live-process permission failures must propagate.
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    raise
        return dict(seconds=time.monotonic(), rss=rss, fds=fds, threads=threads)

    def _sample(self):
        while not self.done.wait(.02):
            try:
                self.samples.append(self.snapshot())
            except psutil.NoSuchProcess:
                return
            except Exception as error:
                self.sampling_errors.append(repr(error))
                return

    def metrics(self):
        status, body = fetch(self.base, '/__terracotta/metrics')
        assert status == 200
        return json.loads(body)

    def resources(self):
        assert not self.sampling_errors, self.sampling_errors
        assert self.samples, 'resource sampling produced no observations'
        return {f'peak_{key}': max((x[key] for x in self.samples), default=0)
                for key in ['rss', 'fds', 'threads']}

    def close(self):
        self.done.set(); self.thread.join(timeout=2)
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
        self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
