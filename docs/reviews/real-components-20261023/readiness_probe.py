import subprocess,time,json
D=['docker','--context','colima-ailab-verify']
ids=subprocess.check_output(D+['ps','-q','--filter','label=io.kubernetes.pod.namespace=platform'],text=True).split();data=json.loads(subprocess.check_output(D+['inspect',*ids],text=True));apps={c['Config']['Labels']['io.kubernetes.container.name']:c['Id'] for c in data}
probe='''import httpx,time,json
for name,port in [("api",8080),("agent",8082),("worker",8083)]:
 start=time.monotonic()
 r=httpx.get("http://platform-"+name+":"+str(port)+"/readyz",timeout=5,trust_env=False)
 print(name,r.status_code,json.dumps(r.json()),round(time.monotonic()-start,3),flush=True)
 assert r.status_code==(200 if name=="api" else 503)
 if name=="api":assert r.json()["degraded"] is True
'''
subprocess.run(D+['pause',apps['redis']],check=True,stdout=subprocess.DEVNULL)
try:
 subprocess.run(D+['exec',apps['broker'],'python','-c',probe],check=True)
finally:subprocess.run(D+['unpause',apps['redis']],check=True,stdout=subprocess.DEVNULL)
probe='''import httpx
for name,port in [("api",8080),("agent",8082),("worker",8083)]:
 r=httpx.get("http://platform-"+name+":"+str(port)+"/readyz",timeout=5,trust_env=False);assert r.status_code==200,(name,r.status_code);print("RECOVERED",name,200)
'''
subprocess.run(D+['exec',apps['broker'],'python','-c',probe],check=True)
