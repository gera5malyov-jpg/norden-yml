#!/usr/bin/env python3
"""Guarded removal of broken callback link after browser testing.

Removes unsupported link while retaining contact phone in trusted purchase box.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
source=file.read_text(encoding='utf-8')
unsafe_link='''              <p class="megapolis-purchase-consultation">
                <a href="#callback-modal">Уточнить стоимость и срок доставки</a>
              </p>
'''
if source.count(unsafe_link)!=1 or source.count('class="megapolis-purchase-trust"')!=1:
 raise RuntimeError('Active product trust box is different; stop before changes')
if 'class="product-page__buy-help"' in source:
 raise RuntimeError('Repeat-duplicate fix was not applied; stop')
update=source.replace(unsafe_link,'',1)
folder=root.parent/'storefront-backups'/('stage45-broken-callback-removal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700,parents=True)
backup=folder/'product.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.stage45-',delete=False) as f:
  f.write(update);temp=f.name
 os.chmod(temp,st.st_mode);os.chown(temp,st.st_uid,st.st_gid);os.replace(temp,str(file));written=True
 tests=[]
 for route,kind in (
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','categorized'),
  ('/stul-sevyn-bukle-kofeynyy-2-sht/','uncategorized'),
  ('/category/kompyuternye-kresla/','category'),
  ('/order/','checkout')
 ):
  p=subprocess.Popen(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',
   'https://profikompany.ru'+route+'?stage45_verify='+str(os.getpid())],
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,_=p.communicate(timeout=43)
  html,status=out.rsplit('\n',1) if '\n' in out else ('','')
  no_unsafe='class="megapolis-purchase-consultation"' not in html and 'Уточнить стоимость и срок доставки' not in html
  if kind=='categorized':
   ok=p.returncode==0 and status=='200' and no_unsafe and html.count('class="megapolis-purchase-trust"')==1 and 'class="product-page__buy-help"' not in html and 'features features--inline features--circle' not in html and 'href="tel:+78122449264"' in html and 'class="product-add"' in html
  elif kind=='uncategorized':
   ok=p.returncode==0 and status=='200' and no_unsafe and 'class="megapolis-purchase-trust"' not in html and 'features features--inline features--circle' in html
  else:
   ok=p.returncode==0 and status=='200'
  tests.append({'url':route,'http':status,'ok':ok})
 if not all(x['ok'] for x in tests):raise RuntimeError('Live smoke failed '+json.dumps(tests))
 print('STAGE45_APPLY='+json.dumps({'ok':True,'backup':str(backup),'tests':tests},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE45_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
 raise
PY
"""

def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels gateway token missing')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage45-broken-callback-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage45-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE45_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
