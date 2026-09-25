"""Compare native TOML/env settings to the frozen Python parser, with dummy credentials."""
import json, os, subprocess, tempfile
from pathlib import Path
import toml
from common import ROOT, write_json
from terracotta.config import parse_config
OUT=Path(os.environ.get('TC_SETTINGS_OUT',ROOT/'validation/evidence/p07/settings.json'))
BIN=Path(os.environ.get('TC_P07_BINARY',ROOT/'target/debug/terracotta')).resolve()
checks=[]
def run(name,config=None,environment=None,flags=None):
    env={k:v for k,v in os.environ.items() if not k.startswith('TC_')}
    env.update(environment or {})
    with tempfile.TemporaryDirectory(dir=ROOT/'validation/artifacts') as folder:
        config=config or {}
        path=Path(folder);(path/'config.toml').write_text(toml.dumps(config));(path/'request.json').write_text('{"operation":"settings"}')
        result=subprocess.run([str(BIN),'-c',str(path/'config.toml'),*(flags or []),'data-operation',str(path/'request.json')],env=env,capture_output=True,text=True)
        expected=subprocess.run([str(ROOT/'.venv-reference/bin/python'),'-c','import json,terracotta.config as c,sys;print(json.dumps(c.parse_config(json.loads(sys.argv[1]))._asdict()))',json.dumps(config)],env=dict(env,PYTHONPATH=str(ROOT/'.cache/reference')),capture_output=True,text=True)
        if flags:
            oracle=json.loads(expected.stdout);oracle['LOGLEVEL']=flags[-1]
        else:oracle=json.loads(expected.stdout) if expected.returncode==0 else None
        actual=json.loads(result.stdout) if result.returncode==0 else None
        passed=(result.returncode==0)==(expected.returncode==0) and actual==oracle
        checks.append(dict(name=name,passed=passed,native_exit=result.returncode,oracle_exit=expected.returncode))
        write_json(OUT,{'checks':checks})
        if not passed:raise AssertionError((name,actual,oracle,result.stderr,expected.stderr))
run('all-defaults')
run('config-over-env',{'DEFAULT_TILE_SIZE':[64,96],'LOGLEVEL':'info'},{'TC_DEFAULT_TILE_SIZE':'[128,256]','TC_LOGLEVEL':'error'})
run('cli-over-config',{'LOGLEVEL':'warning'},flags=['--loglevel','debug'])
values=parse_config()._asdict()
for key,value in values.items():
    if value is None:value='test' if key!='DRIVER_PROVIDER' else 'sqlite'
    elif isinstance(value,bool):value=not value
    elif isinstance(value,int):value=2
    elif isinstance(value,(list,tuple)):value=['https://example.test'] if 'ORIGINS' in key else [128,96]
    elif key=='LOGLEVEL':value='debug'
    elif key.endswith('_METHOD'):value='nearest'
    elif key=='REMOTE_DB_CACHE_DIR':value='/tmp/p07-settings-cache'
    else:value='test'
    run('env-'+key,environment={'TC_'+key:value if isinstance(value,str) else json.dumps(value)})
for old in ['MYSQL_USER','MYSQL_PASSWORD','POSTGRESQL_USER','POSTGRESQL_PASSWORD']:
    run('deprecated-precedence-'+old,environment={'TC_'+old:'old','TC_SQL_'+old.split('_')[-1]:'new'})
for name,config in [('unknown',{'BOGUS':1}),('invalid-bool',{'DEBUG':'maybe'}),('negative-cache',{'RASTER_CACHE_SIZE':-1}),('png-range',{'PNG_COMPRESS_LEVEL':10}),('shape',{'DEFAULT_TILE_SIZE':[1]}),('resampling',{'RESAMPLING_METHOD':'magic'}),('bad-json',{'ALLOWED_ORIGINS_TILES':'['})]:run(name,config)
for name,config in [('integer-float',{'RASTER_CACHE_SIZE':2.9}),('integer-strings',{'DEFAULT_TILE_SIZE':['64','96']}),('missing-cache-parent',{'REMOTE_DB_CACHE_DIR':'/definitely-missing-p07-parent/cache'})]:run(name,config)
print(f'{len(checks)} settings comparisons passed')
