"""TLS options on disposable PostgreSQL; reproduce legacy MySQL URL-query bug."""
import uuid
from urllib.parse import urlencode
import p06_validate as p
p.OUT=p.ROOT/'process/validation/artifacts/p06-tls';p.OUT.mkdir(exist_ok=True)
from p06_validate import *

def run():
    url=BASE['postgresql']+'/p06_'+uuid.uuid4().hex
    operation(url,'create',keys=[dict(key='x',description='')])
    cert=str(ROOT/'process/validation/artifacts/p06/postgres-ca.crt')
    for mode,ca,host,success in [('disable',False,'127.0.0.1',True),('prefer',False,'127.0.0.1',True),('require',False,'127.0.0.1',True),('verify-ca',True,'127.0.0.1',True),('verify-full',True,'localhost',True),('verify-full',True,'127.0.0.1',False),('verify-full',False,'localhost',False)]:
        params={'sslmode':mode}
        if ca:params['sslrootcert']=cert
        path=url.replace('127.0.0.1',host)+'?'+urlencode(params)
        r=operation(path,'keys',False)
        tc.drivers._DRIVER_CACHE.clear()
        try:tc.get_driver(path).get_keys();oracle=True
        except Exception:oracle=False
        check('PostgreSQL TLS '+str((mode,ca,host)),oracle==success and (isinstance(r,list))==success,{'oracle':oracle,'rust':r if isinstance(r,list) else r.stderr})
    mysql=BASE['mysql']+'/mysql?ssl_ca='+str(ROOT/'process/validation/artifacts/p06/mysql-ca.pem')
    try:tc.get_driver(mysql).get_keys();failed=False
    except Exception as e:failed=True;REPORT['mysql_query_exception']=type(e).__name__+': '+str(e)
    r=operation(mysql,'keys',False)
    check('MySQL legacy query suffix error',failed and r.returncode!=0)
    write_json(OUT/'report.json',REPORT);print(len(REPORT['checks']),'TLS/query checks passed')
if __name__=='__main__':run()
