import base64,json,subprocess,time,httpx
from pathlib import Path
private=json.loads(Path('/tmp/ailab-real-verify-20261023/platform-secrets-private.json').read_text());token=base64.b64decode(private['data']['AILAB_DEV_TOKEN']).decode()
c=httpx.Client(base_url='http://127.0.0.1:30080',headers={'Authorization':'Bearer '+token},timeout=10)
ids=subprocess.check_output(['docker','--context','colima-ailab-verify','ps','-q','--filter','label=io.kubernetes.container.name=clamd','--filter','label=io.kubernetes.pod.namespace=platform'],text=True).split();assert len(ids)==1
start=time.monotonic();subprocess.run(['docker','--context','colima-ailab-verify','pause',ids[0]],check=True,stdout=subprocess.DEVNULL)
try:
 r=c.post('/v1/files',files={'upload':('outage.txt',b'clean document during real scanner outage','text/plain')});r.raise_for_status();fid=r.json()['id']
 while time.monotonic()-start<40:
  f=c.get('/v1/files/'+fid).json()
  if (f.get('meta') or {}).get('processing')=='scan_unavailable':break
  time.sleep(.5)
 assert f['status']=='pending' and f['meta']['processing']=='scan_unavailable',f
 print('OUTAGE',f['status'],f['meta']['processing'],'attempts',f['meta'].get('scan_attempts'),'seconds',round(time.monotonic()-start,3),flush=True)
finally:subprocess.run(['docker','--context','colima-ailab-verify','unpause',ids[0]],check=True,stdout=subprocess.DEVNULL)
while time.monotonic()-start<90:
 f=c.get('/v1/files/'+fid).json()
 if f['status']=='ready':break
 time.sleep(.5)
assert f['status']=='ready',f
print('RECOVERY',f['status'],'verdict',f['meta']['scan']['verdict'],'seconds',round(time.monotonic()-start,3),flush=True);c.close()
