import json,time,resource
from pathlib import Path
root=Path('/Users/michael/.cache/ailab-real-verify-20261023');mapping=json.loads((root/'pod-map.json').read_text());records={};samples=0;started=time.monotonic()
while not (root/'sampler-stop').exists():
 phase=(root/'phase').read_text().strip();samples+=1
 for pod in mapping:
  try:
   cg=Path('/sys/fs/cgroup')/Path('/proc',str(pod['pid']),'cgroup').read_text().strip().split('::')[1].lstrip('/')
   pids=[int(x) for x in (cg/'cgroup.procs').read_text().split()];rss=0
   for pid in pids:
    try:
     line=next(x for x in Path('/proc',str(pid),'status').read_text().splitlines() if x.startswith('VmRSS:'));rss+=int(line.split()[1])*1024
    except (OSError,StopIteration):pass
   current=int((cg/'memory.current').read_text());events=dict(line.split() for line in (cg/'memory.events').read_text().splitlines());cpu=dict(line.split() for line in (cg/'cpu.stat').read_text().splitlines())
  except OSError:continue
  key=phase+'/'+pod['pod'];r=records.setdefault(key,{'pod':pod['pod'],'phase':phase,'limit':pod['limit'],'rss_peak':0,'cgroup_peak_sampled':0,'oom_kill':0,'throttled_start':int(cpu.get('throttled_usec',0)),'throttled_end':0})
  r['rss_peak']=max(r['rss_peak'],rss);r['cgroup_peak_sampled']=max(r['cgroup_peak_sampled'],current);r['oom_kill']=max(r['oom_kill'],int(events['oom_kill']));r['throttled_end']=int(cpu.get('throttled_usec',0))
 if samples % 10 == 0:
  snapshot={'elapsed':time.monotonic()-started,'samples':samples,'records':list(records.values())}
  (root/'resources.partial.json').write_text(json.dumps(snapshot,indent=2))
 time.sleep(.1)
result={'elapsed':time.monotonic()-started,'samples':samples,'sampler_cpu_seconds':resource.getrusage(resource.RUSAGE_SELF).ru_utime+resource.getrusage(resource.RUSAGE_SELF).ru_stime,'records':list(records.values())}
(root/'resources.json').write_text(json.dumps(result,indent=2));print('samples',samples,'seconds',round(result['elapsed'],3),'sampler_cpu',round(result['sampler_cpu_seconds'],3))
