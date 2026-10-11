#!/usr/bin/env python3
"""Read-only audit for duplicate purchase trust and features components."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
dirs=[
root/'wa-data/public/shop/themes/pureMegapolis42',
root/'wa-data/public/site/themes/pureMegapolis42'
]
patterns=['product-page__buy-help','megapolis-purchase-trust','megapolis-purchase-consultation',
          'features--inline','features__item','Платите как удобно','Бережная доставка','Гарантия производителя',
          'Страховка при отправке','product__features','product__advantages']
for d in dirs:
 for f in d.rglob('*.html'):
  if f.stat().st_size>220000:continue
  lines=f.read_text(encoding='utf-8',errors='replace').splitlines()
  hits=[(i,n) for i,n in enumerate(lines) if any(x in n for x in patterns)]
  if not hits:continue
  out=[]
  for i,n in hits:
   out.append({'line':i+1,'surrounding':[{'n':j+1,'text':lines[j][:430]} for j in range(max(0,i-3),min(len(lines),i+5))]})
  print('STAGE41_AUDIT='+json.dumps({'file':str(f.relative_to(root)), 'matches':out[:30]},ensure_ascii=False))
PY
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage41-audit-read-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage41-audit-'+os.getenv('GITHUB_RUN_ID','audit')}).encode(),headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=150)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE41_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact audit failed: '+p.stdout[-400:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
