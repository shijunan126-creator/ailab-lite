import base64,hashlib,json,subprocess,time
from pathlib import Path
root=Path('/Users/michael/.cache/ailab-real-verify-20261023')
def docker(*args,**kw):return subprocess.run(['docker','--context','colima-ailab-verify',*args],check=True,text=True,**kw)
ids=docker('ps','-q','--filter','label=io.kubernetes.pod.namespace=platform',capture_output=True).stdout.split()
containers=json.loads(docker('inspect',*ids,capture_output=True).stdout)
apps={c['Config']['Labels']['io.kubernetes.container.name']:c['Id'] for c in containers if c['Config']['Labels']['io.kubernetes.container.name'] in ['api','agent','worker','broker']}
assert len(apps)==4
paused=[]
code='''import json,time,resource
from pathlib import Path
from ailab_platform.ops.backup import create,verify,restore
start=time.monotonic();b=create(Path('/evidence/k3s-backup'));t1=time.monotonic();v=verify(Path('/evidence/k3s-backup'));t2=time.monotonic();r=restore(Path('/evidence/k3s-backup'),Path('/evidence/k3s-restored'));t3=time.monotonic();v2=verify(Path('/evidence/k3s-restored'));t4=time.monotonic()
print(json.dumps({'create':b,'verify':v,'restore':r,'verify_restored':v2,'seconds':{'create':t1-start,'verify':t2-t1,'restore':t3-t2,'verify_restored':t4-t3},'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024},ensure_ascii=False))
'''
try:
 for cid in apps.values():docker('pause',cid,stdout=subprocess.DEVNULL);paused.append(cid)
 start=time.monotonic()
 r=docker('run','--rm','--name','verify-backup','--user','0','--network','none','--memory','256m','--cpus','0.5','--volumes-from',apps['api']+':ro','-v',str(root)+':/evidence','-e','AILAB_ENV_FILE=/tmp/no-developer-env','-e','AILAB_PROFILE=test','ailab-platform:verify','python','-c',code,capture_output=True)
 print(r.stdout,flush=True);print('TOTAL_WALL_SECONDS',round(time.monotonic()-start,3),flush=True)
finally:
 for cid in reversed(paused):docker('unpause',cid,stdout=subprocess.DEVNULL)
