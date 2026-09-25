import concurrent.futures,json,statistics,time,subprocess,psutil
from pathlib import Path
from p08_runtime import Service,fetch,image_hash,DATABASE,ROOT
from p08_benchmark import workloads
out=ROOT/'validation/artifacts/p08-bottleneck'; cpus=psutil.Process().cpu_affinity()[:3]
results=[];gold={}
for name,nproc,workers,gdal_cache in [('shared-32',1,3,32),('shared-96',1,3,96),('isolated-32-total',3,1,10),('isolated-96-total',3,1,32)]:
 services=[Service(DATABASE,out/(name+'-'+str(i)),cache=0,cpus=cpus,extra={'TC_RASTER_WORKERS':str(workers),'GDAL_CACHEMAX':str(gdal_cache)}) for i in range(nproc)]
 try:
  for workload in ['preview','geographic-dem']:
   path=workloads()[workload]
   def request(i):
    s=services[i%len(services)];start=time.perf_counter();status,body=fetch(s.base,path);elapsed=time.perf_counter()-start
    assert status==200
    digest=image_hash(body)
    if workload in gold:assert digest==gold[workload]
    else:gold[workload]=digest
    return elapsed
   for i in range(6):request(i)
   cpu_before=sum(s.root.cpu_times().user+s.root.cpu_times().system for s in services)
   start=time.perf_counter()
   with concurrent.futures.ThreadPoolExecutor(3) as pool:samples=list(pool.map(request,range(18)))
   wall=time.perf_counter()-start
   cpu_after=sum(s.root.cpu_times().user+s.root.cpu_times().system for s in services)
   record=dict(configuration=name,workload=workload,p50_ms=statistics.median(samples)*1000,wall_seconds=wall,cpu_seconds=cpu_after-cpu_before,mean_cpu_cores=(cpu_after-cpu_before)/wall,samples=samples)
   results.append(record);print(json.dumps(record),flush=True)
  if name=='shared-32':
   command=['perf','record','-F','99','-g','-p',str(services[0].process.pid),'-o',str(out/'perf.data'),'--','sleep','8']
   with (out/'perf-record.log').open('w') as log:
    profiler=subprocess.Popen(command,stdout=log,stderr=log)
    with concurrent.futures.ThreadPoolExecutor(3) as pool:list(pool.map(lambda i:fetch(services[0].base,workloads()['preview']),range(18)))
    profiler.wait()
 finally:
  for s in services:s.close()
 (out/'report.json').write_text(json.dumps(results,indent=2)+'\n')
