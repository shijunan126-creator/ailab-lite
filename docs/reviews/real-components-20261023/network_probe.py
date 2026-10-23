import subprocess,json
k='/tmp/ailab-real-verify-20261023/kubectl'
def run(args):return subprocess.run([k,'-n','platform',*args],capture_output=True,text=True,timeout=30)
code='''import socket,json,time
results={}
for host,port in [("platform-clamav",3310),("platform-broker",8081)]:
 start=time.monotonic()
 try:
  s=socket.create_connection((host,port),2);s.close();result="connected"
 except OSError as e: result=type(e).__name__
 results[host]={"result":result,"seconds":round(time.monotonic()-start,3)}
print(json.dumps(results))'''
for service in ['agent','worker']:
 r=run(['exec','deploy/platform-'+service,'--','python','-c',code]);print(service,r.returncode,r.stdout.strip(),r.stderr.strip())
r=run(['get','deploy','-o','json']);data=json.loads(r.stdout)
for dep in data['items']:
 for c in dep['spec']['template']['spec']['containers']:
  names=[s['secretRef']['name'] for s in c.get('envFrom',[]) if 'secretRef' in s]
  refs=[{'env':e['name'],'secret':e['valueFrom']['secretKeyRef']['name']} for e in c.get('env',[]) if 'secretKeyRef' in e.get('valueFrom',{})]
  print('SECRET_REFS',dep['metadata']['name'],names,refs)
import httpx
for path in ['/internal/runs/not-public','/metrics','/docs','/readyz']:
 r=httpx.get('http://127.0.0.1:30080'+path,timeout=5);print('PUBLIC',path,r.status_code)
