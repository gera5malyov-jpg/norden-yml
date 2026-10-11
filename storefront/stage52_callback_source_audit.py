#!/usr/bin/env python3
"""Clean mobile header: hide duplicate hamburger and header callback; keep both numbers.

Guarded edits of two active theme templates, server backups and rollback.
No database, product, order, pricing or marketplace writes.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
base=root/'wa-data/public/site/themes/pureMegapolis42'
shop=root/'wa-data/public/shop/themes/pureMegapolis42'
for folder in (base,shop):
 for f in folder.rglob('*.html'):
  if f.stat().st_size>280000:continue
  s=f.read_text(encoding='utf-8',errors='replace')
  pattern=re.compile('callback|заказать звонок|обратн.{0,5} звон',re.I)
  matches=list(pattern.finditer(s))
  if not matches:continue
  samples=[{'offset':m.start(),'near':' '.join(s[max(0,m.start()-170):m.end()+190].split())[:430]} for m in matches[:15]]
  print('STAGE52_MATCH='+json.dumps({'file':str(f.relative_to(root)),'count':len(matches),'samples':samples},ensure_ascii=False),flush=True)
for rel in ['wa-data/public/site/themes/pureMegapolis42/index.html','wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html','wa-data/public/site/themes/pureMegapolis42/components/component.phones.html']:
 f=root/rel;s=f.read_text(encoding='utf-8')
 lines=s.splitlines()
 needle='callback'
 hits=[i for i,line in enumerate(lines) if needle in line.lower()]
 windows=[]
 for i in hits:
  a=max(0,i-4);b=min(len(lines),i+6)
  if windows and a<=windows[-1][1]+2:windows[-1]=(windows[-1][0],max(windows[-1][1],b))
  else:windows.append((a,b))
 print('STAGE52_LINES='+json.dumps({'file':rel,'length':len(lines),'windows':[{'start':a+1,'lines':lines[a:b][:80]} for a,b in windows[:15]]},ensure_ascii=False),flush=True)

PY

"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage52-audit-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage52-audit-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=190)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE52_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact deployment failed '+p.stdout[-2000:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
