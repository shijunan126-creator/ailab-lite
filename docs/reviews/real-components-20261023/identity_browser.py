from pathlib import Path
import httpx,json
from playwright.sync_api import sync_playwright
base='http://127.0.0.1:14770'
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True);context=browser.new_context();p=context.new_page()
 p.goto(base+'/login');p.get_by_role('link',name='使用统一身份登录').click();p.locator('#username').fill('verify-user');p.locator('#password').fill('disposable-verify-only');p.locator('input[type=submit],button[type=submit]').first.click();p.wait_for_url(base+'/',timeout=30000)
 r=context.request.get(base+'/api/v1/me');assert r.status==200,r.text();me=r.json();assert set(me['roles'])=={'user','dept_admin','auditor'},me;assert me['dept']['id']=='verify-dept',me
 cookies=context.cookies();cookie=next(c for c in cookies if c['name']=='ailab_token');assert cookie['httpOnly'];assert cookie['sameSite']=='Strict';print('LOGIN',json.dumps(me,ensure_ascii=False),'httpOnly',cookie['httpOnly'],'sameSite',cookie['sameSite'],flush=True)
 p.screenshot(path='/Users/michael/.cache/ailab-real-verify-20261023/keycloak-portal.png',full_page=True)
 r=context.request.post(base+'/api/auth/logout');assert r.status==200;assert context.request.get(base+'/api/v1/me').status==401;print('LOGOUT platform session revoked',flush=True)
 # Disable through the actual admin API, then retry an IdP-authenticated login.
 a=httpx.Client(base_url='http://127.0.0.1:14880',timeout=10);r=a.post('/v1/auth/login',json={'username':'verify-admin','password':'verify-admin-only'});r.raise_for_status();a.headers['Authorization']='Bearer '+r.json()['token'];r=a.patch('/v1/admin/users/'+me['id'],json={'disabled':True});r.raise_for_status()
 p.goto(base+'/api/auth/oidc/start');p.wait_for_url(base+'/login?error=oidc',timeout=30000);assert context.request.get(base+'/api/v1/me').status==401;print('DISABLED refused at Portal callback and /me=401',flush=True)
 r=a.patch('/v1/admin/users/'+me['id'],json={'disabled':False});r.raise_for_status();a.close();browser.close()
