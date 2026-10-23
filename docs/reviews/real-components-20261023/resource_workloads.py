import base64,json,subprocess,time,httpx
from pathlib import Path
root=Path('/Users/michael/.cache/ailab-real-verify-20261023')
private=json.loads(Path('/tmp/ailab-real-verify-20261023/platform-secrets-private.json').read_text());token=base64.b64decode(private['data']['AILAB_DEV_TOKEN']).decode()
private=json.loads(Path('/tmp/ailab-real-verify-20261023/agent-private.json').read_text());agent=base64.b64decode(private['data']['AILAB_SERVICE_CREDENTIAL']).decode()
c=httpx.Client(base_url='http://127.0.0.1:30080',headers={'Authorization':'Bearer '+token},timeout=180)
a=httpx.Client(base_url='http://127.0.0.1:14881',headers={'X-Service-Token':agent},timeout=180)
b=httpx.Client(base_url='http://127.0.0.1:8081',headers={'X-Service-Token':agent},timeout=180)
results={}
def phase(name):(root/'phase').write_text(name)
def checked(r):
 if r.status_code>=400:raise RuntimeError(str(r.status_code)+' '+r.text[:800])
 return r.json() if r.content else None
def upload(path):
 with path.open('rb') as f:return checked(c.post('/v1/files',files={'upload':(path.name,f,'application/octet-stream')}))
def waitfile(fid,deadline=180):
 end=time.monotonic()+deadline
 while time.monotonic()<end:
  f=checked(c.get('/v1/files/'+fid))
  if f['status']!='pending':return f
  time.sleep(.25)
 raise RuntimeError('file timeout '+str(f))
def record(name,start,**kw):
 results[name]={'seconds':round(time.monotonic()-start,3),**kw};print(name,json.dumps(results[name],ensure_ascii=False),flush=True)
paused=None;lease=None;rid=None
try:
 time.sleep(5)
 for name,path in [('upload50',root/'fixtures/50m.bin'),('parse20',root/'fixtures/20m.pptx')]:
  phase(name);start=time.monotonic();f=waitfile(upload(path)['id']);assert f['status']=='ready',f;results[name+'_file']=f
  record(name,start,bytes=path.stat().st_size,extract=f['meta'].get('extract'))
 phase('docx-render');start=time.monotonic();f=waitfile(upload(root/'cjk/chinese.docx')['id'])
 while time.monotonic()-start<180:
  p=checked(c.get('/v1/files/'+f['id']+'/preview'))
  if p['status']!='pending':break
  time.sleep(.5)
 assert p['status']=='ready',p;record('docx-render',start,preview=p)
 ids=subprocess.check_output(['docker','--context','colima-ailab-verify','ps','-q','--filter','label=io.kubernetes.container.name=agent','--filter','label=io.kubernetes.pod.namespace=platform'],text=True).split();assert len(ids)==1;paused=ids[0]
 subprocess.run(['docker','--context','colima-ailab-verify','pause',paused],check=True,stdout=subprocess.DEVNULL)
 rid=checked(c.post('/v1/runs',json={'prompt':'资源验收受控工具调用','model':'ailab-demo','file_ids':[results['upload50_file']['id']]}))['id']
 checked(a.post('/internal/runs/'+rid+'/status',json={'status':'running'}))
 phase('inject50');start=time.monotonic();lease=checked(b.post('/internal/leases',json={'run_id':rid}))['lease_id'];record('inject50',start,bytes=50*1024**2)
 phase('artifact100');start=time.monotonic();out=checked(b.post('/internal/leases/'+lease+'/exec',json={'cmd':'truncate -s 104857600 /workspace/outputs/100m.bin','timeout_seconds':10}));assert out['exit_code']==0,out
 artifact=checked(b.post('/internal/leases/'+lease+'/commit',json={'path':'/workspace/outputs/100m.bin','filename':'100m.bin'}));assert artifact['size']==100*1024**2,artifact
 record('artifact100',start,bytes=artifact['size']);results['artifact']=artifact
 checked(a.post('/internal/runs/'+rid+'/status',json={'status':'succeeded','summary':'资源探针完成'}))
except Exception as e:
 results['failure']={'type':type(e).__name__,'message':str(e)};print('FAILURE',results['failure'],flush=True)
finally:
 if lease:
  try:b.delete('/internal/leases/'+lease)
  except Exception:pass
 if paused:subprocess.run(['docker','--context','colima-ailab-verify','unpause',paused],stdout=subprocess.DEVNULL)
 phase('after');time.sleep(2);(root/'sampler-stop').touch();(root/'resource-workloads.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));c.close();a.close();b.close()
