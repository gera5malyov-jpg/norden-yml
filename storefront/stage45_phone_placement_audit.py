#!/usr/bin/env python3
"""Read-only scan of theme phone placements and header structure."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
directories=[root/'wa-data/public/site/themes/pureMegapolis42',root/'wa-data/public/shop/themes/pureMegapolis42']
patterns=['$_company__phone','phones__link--phone','company__phone','header-top','phones--header','phone__number','812','499','78122449264','74996776332','megapolis-purchase-trust__contact','tel:']
for folder in directories:
 for f in folder.rglob('*.html'):
  if f.stat().st_size>170000:continue
  lines=f.read_text(encoding='utf-8',errors='replace').splitlines()
  hits=[i for i,line in enumerate(lines) if any(x in line for x in patterns)]
  if not hits:continue
  result=[]
  for i in hits[:30]:
   result.append({'line':i+1,'context':[{'n':j+1,'text':lines[j][:420]} for j in range(max(0,i-2),min(len(lines),i+3))]})
  print('STAGE45_THEME='+json.dumps({'file':str(f.relative_to(root)),'hits':result},ensure_ascii=False))
for rel,a,b in (
 ('wa-data/public/site/themes/pureMegapolis42/components/component.phones.html',1,65),
 ('wa-data/public/site/themes/pureMegapolis42/layouts/layout.welcome.html',1,30),
 ('wa-data/public/shop/themes/pureMegapolis42/product.html',144,180),
 ('wa-data/public/site/themes/pureMegapolis42/header/header.top.html',75,123),
):
 p=root/rel
 if p.is_file():
  lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
  print('STAGE45_SNIPPET='+json.dumps({'file':rel,'lines':[{'n':i+1,'t':lines[i][:400]} for i in range(a-1,min(b,len(lines)))]},ensure_ascii=False))
PY
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage45-audit-read-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  from urllib.error import HTTPError
  import time
  for attempt in range(1,11):
   try:
    resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage45-audit-'+os.getenv('GITHUB_RUN_ID','audit')}).encode(),headers)
    break
   except HTTPError as e:
    details=e.read().decode('utf-8','replace')
    if e.code==400 and 'SSH' in details and attempt<10:
     print('STAGE45_SERVER_BACKUP='+json.dumps({'attempt':attempt,'retry_seconds':25}),flush=True)
     time.sleep(25)
     continue
    print('STAGE45_SSH_API_ERROR='+json.dumps({'status':e.code,'detail':details[:450]}),flush=True)
    raise
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=150)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE45_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact audit failed: '+p.stdout[-400:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
