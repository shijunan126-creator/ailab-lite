import base64,io,json,time
from pathlib import Path
import httpx
from PIL import Image
private=json.loads(Path('/tmp/ailab-real-verify-20261023/platform-secrets-private.json').read_text())
token=base64.b64decode(private['data']['AILAB_DEV_TOKEN']).decode()
c=httpx.Client(base_url='http://127.0.0.1:30080',headers={'Authorization':'Bearer '+token},timeout=180)
def upload(name,data,ctype):
 r=c.post('/v1/files',files={'upload':(name,data,ctype)});r.raise_for_status();return r.json()
def waitfile(fid,timeout=180):
 start=time.monotonic()
 while time.monotonic()-start<timeout:
  r=c.get('/v1/files/'+fid);r.raise_for_status();f=r.json()
  if f['status']!='pending': print('FILE',f['filename'],f['status'],round(time.monotonic()-start,3),f.get('meta',{}).get('scan',{}).get('verdict'),flush=True);return f
  time.sleep(.5)
 raise RuntimeError('file deadline '+str(f))
f=waitfile(upload('clean.txt',b'clean document','text/plain')['id']);assert f['status']=='ready'
eicar=b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
f=waitfile(upload('eicar.com',eicar,'application/octet-stream')['id']);assert f['status']=='rejected'
f=waitfile(upload('chinese.docx',Path('/Users/michael/.cache/ailab-real-verify-20261023/cjk/chinese.docx').read_bytes(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')['id']);assert f['status']=='ready'
start=time.monotonic()
while time.monotonic()-start<180:
 r=c.get('/v1/files/'+f['id']+'/preview');r.raise_for_status();p=r.json()
 if p['status']!='pending':break
 time.sleep(1)
assert p['status']=='ready',p
asset=c.get('/v1/files/'+f['id']+'/preview/'+p['pages'][0]);asset.raise_for_status();Path('/Users/michael/.cache/ailab-real-verify-20261023/k3s-docx.jpg').write_bytes(asset.content)
print('LIBRARY_DOCX',p,'seconds',round(time.monotonic()-start,3),flush=True)
r=c.post('/v1/runs',json={'prompt':'做一个 PPT','model':'ailab-demo','mode':'work'});r.raise_for_status();rid=r.json()['id'];start=time.monotonic()
while time.monotonic()-start<240:
 r=c.get('/v1/runs/'+rid);r.raise_for_status();run=r.json()
 if run['status'] in ['succeeded','failed','canceled']:break
 time.sleep(1)
print('WORK',run['status'],'seconds',round(time.monotonic()-start,3),'error',run.get('error'),'artifacts',run.get('artifacts'),flush=True)
assert run['status']=='succeeded',run
Path('/Users/michael/.cache/ailab-real-verify-20261023/stack-probe-result.json').write_text(json.dumps({'docx':f,'preview':p,'run':run},ensure_ascii=False))
c.close()
