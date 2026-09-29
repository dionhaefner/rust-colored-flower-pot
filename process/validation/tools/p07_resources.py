"""Owner release and real TCP disconnect/native-drain checks (Linux)."""
import gc, json, os, socket, subprocess, time
from pathlib import Path
from common import ROOT, write_json
from p02_failure import prepare_database, wait_ready, metrics, fetch
from range_server import RangeServer
from terracotta_rust import NativeApplication

OUT=Path(os.environ.get('TC_RESOURCE_OUT',ROOT/'process/validation/artifacts/p07-resources'));OUT.mkdir(parents=True,exist_ok=True)
DB=ROOT/'process/validation/artifacts/capture-reproduction/reference.sqlite'
BIN=Path(os.environ.get('TC_P07_BINARY',ROOT/'target/debug/terracotta'))
def count():return dict(threads=len(list(Path('/proc/self/task').iterdir())),fds=len(list(Path('/proc/self/fd').iterdir())))
warm=NativeApplication(DB);warm.request('GET','/keys');del warm;gc.collect();time.sleep(.3)
before=count()
for i in range(20):
    app=NativeApplication(DB,dict(RASTER_WORKERS=2))
    assert app.request('GET','/singleband/lagoon/red/preview.png')[0]==200
    del app
gc.collect()
deadline=time.monotonic()+5
while time.monotonic()<deadline and count()['threads']>before['threads']:time.sleep(.05)
after=count()
assert after['threads']<=before['threads'] and after['fds']<=before['fds']+2,(before,after)
server=RangeServer(ROOT/'.cache/corpus',0,delay_seconds=.03)
database=OUT/'disconnect.sqlite'
if database.exists():database.unlink()
prepare_database(database,server.port)
base='http://127.0.0.1:5078'
with (OUT/'server.log').open('w') as log:
    process=subprocess.Popen([str(BIN),'serve','-d',str(database),'--port','5078','--profile'],cwd=ROOT,
        env=dict(os.environ,TC_RASTER_WORKERS='1',TC_RASTER_TIMEOUT_MS='30000'),stdout=log,stderr=log)
    try:
        wait_ready(base,process)
        start=metrics(base)
        sock=socket.create_connection(('127.0.0.1',5078))
        sock.sendall(b'GET /singleband/slow/red/preview.png HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            active=metrics(base)
            if active['submitted']>start['submitted']:break
            time.sleep(.01)
        assert active['submitted']>start['submitted'] and active['in_flight']>0,active
        sock.close()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            drained=metrics(base)
            if drained['in_flight']==0 and drained['completed']+drained['failed']==drained['submitted']:break
            time.sleep(.05)
        assert drained['completed']>start['completed'] and drained['in_flight']==0,drained
        assert fetch(base,'/singleband/lagoon/red/preview.png')[0]==200
    finally:
        process.terminate();process.wait(timeout=10);server.close()
records=[json.loads(line) for line in (OUT/'server.log').read_text().splitlines() if line.startswith('{')]
assert any(r.get('profile') and r.get('elapsed_ms',-1)>=0 for r in records)
report=dict(passed=True,owner_release=dict(iterations=20,before=before,after=after),
    disconnect=dict(before=start,active=active,drained=drained,recovery_status=200),
    semantics='A disconnected client does not cancel GDAL. Work drains, stays accounted and leaves the worker usable; no response retry is promised.')
write_json(OUT/'report.json',report);print(json.dumps(report,indent=2))
