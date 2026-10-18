#!/usr/bin/env python3
"""Stage 12: restore modern Metrica funnel goals without personal data.

Only the active head include and a new JS module are modified. Product, order,
category and stock records are untouched.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
import base64
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
shop=root/'wa-data/public/shop/themes/pureMegapolis42'
paths=[theme/'index.html',shop/'product.html',shop/'products.html',shop/'checkout.html',shop/'cart.html',shop/'order.html']
for d in (shop,theme):
 if d.exists():
  for p in d.glob('**/*checkout*.html'):
   if len(paths)<22: paths.append(p)
for p in paths:
 if not p.is_file(): continue
 s=p.read_text(encoding='utf-8',errors='replace')
 markers=['ecommerce','dataLayer','purchase','order_created','wa_order_form_created','product_id','sku_id','order_id','checkout','cart','schema.org','funnel','metrika']
 chosen=[]
 for n,line in enumerate(s.splitlines(),1):
  if any(x.lower() in line.lower() for x in markers):
   # keep source code fragments, no runtime values
   chosen.append({'line':n,'txt':line[:390]})
 print('FUNNEL_AUDIT='+json.dumps({'file':str(p.relative_to(root)),'size':len(s),'matched':chosen[:28]},ensure_ascii=False),flush=True)
for d in (root/'wa-apps/shop/themes',root/'wa-apps/shop/plugins',root/'wa-data/public/shop/themes/pureMegapolis42/js'):
 if d.exists():
  files=sorted(x.name for x in d.iterdir() if any(q in x.name.lower() for q in ('order','cart','ecom','checkout','analytics','metrika','product')))
  print('FUNNEL_AUDIT='+json.dumps({'directory':str(d.relative_to(root)),'matchingFiles':files[:35]},ensure_ascii=False),flush=True)
PY"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-stage12-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage12-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE.replace('FUNNEL_B64',base64.b64encode((Path(__file__).parent/'assets/metrika_funnel_v1.js').read_bytes()).decode('ascii')),text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('FUNNEL_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
