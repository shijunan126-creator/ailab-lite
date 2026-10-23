import base64,hashlib,json,time
from pathlib import Path
import httpx
root=Path('/Users/michael/.cache/ailab-real-verify-20261023');private=json.loads(Path('/tmp/ailab-real-verify-20261023/platform-secrets-private.json').read_text());token=base64.b64decode(private['data']['AILAB_DEV_TOKEN']).decode();headers={'Authorization':'Bearer '+token}
data=json.loads((root/'stack-probe-result.json').read_text());workloads=json.loads((root/'resource-workloads.json').read_text());artifact=workloads['artifact'];fid=artifact['file_id'];docfid=data['docx']['id']
with httpx.Client(base_url='http://127.0.0.1:14980',headers=headers,timeout=20) as c:
 end=time.monotonic()+60
 while time.monotonic()<end:
  try:
   if c.get('/readyz').status_code==200:break
  except httpx.HTTPError:pass
  time.sleep(.5)
 start=time.monotonic();digest=hashlib.sha256();size=0
 with c.stream('GET','/v1/files/'+fid+'/content') as r:
  r.raise_for_status()
  for chunk in r.iter_bytes():digest.update(chunk);size+=len(chunk)
 assert size==104857600 and digest.hexdigest()==artifact['sha256']
 print('RESTORED_ARTIFACT',size,'sha256 matched','seconds',round(time.monotonic()-start,3),flush=True)
 r=c.get('/v1/files/'+docfid+'/preview');r.raise_for_status();preview=r.json();assert preview['status']=='ready',preview
 r=c.get('/v1/files/'+docfid+'/preview/'+preview['pages'][0]);r.raise_for_status();assert r.content[:2]==b'\xff\xd8';print('RESTORED_PREVIEW',len(r.content),'bytes',flush=True)
 rid=data['run']['id'];r=c.get('/v1/runs/'+rid);r.raise_for_status();assert r.json()['status']=='succeeded';print('RESTORED_RUN succeeded',flush=True)
 print('RESTORED_READYZ',c.get('/readyz').json(),flush=True)
