#!/usr/bin/env python3
"""Stage 17: read-only homepage, checkout text and footer templates audit.

Reads only existing theme and public agreement settings; no writes.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
paths=[root/'wa-data/public/site/themes/pureMegapolis42',root/'wa-data/public/shop/themes/pureMegapolis42']
for base in paths:
 if not base.is_dir():continue
 for p in base.rglob('*.html'):
  if p.stat().st_size>300000:continue
  data=p.read_text(encoding='utf-8',errors='replace')
  words=['welcome-note','pure.easyweb.su/wa-data/public/shop/img/about_2.jpg','условия предоставления услуг','Условия предоставления услуг','service_agreement','footer__link']
  hit=[w for w in words if w in data]
  if not hit:continue
  lines=data.splitlines()
  excerpts=[]
  for i,line in enumerate(lines):
   if any(w in line for w in hit):
    excerpts.append({'n':i+1,'text':line[:700],'nearby':[lines[j][:340] for j in range(max(0,i-2),min(len(lines),i+3))]})
  print('HOMECHECK_TEMPLATE='+json.dumps({'file':str(p.relative_to(root)),'keywords':hit,'excerpts':excerpts[:18]},ensure_ascii=False))
# discover whether homepage text is stored in site_page or shop_page, without private fields
for rel in ('wa-data/public/site/themes/pureMegapolis42/layouts/layout.welcome.html', 'wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html'):
 p=root/rel
 if p.is_file():
  lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
  print('HOMECHECK_CONTEXT='+json.dumps({'file':rel,'lines':[{'n':i+1,'v':lines[i][:510]} for i in range(min(len(lines),110))]},ensure_ascii=False))
print('HOMECHECK_HOME_TEXT_FILES='+json.dumps({
'configs_exist':[(str(p.relative_to(root)),p.is_file()) for p in [
root/'wa-config/apps/site/routes.php',root/'wa-config/apps/site/checkout.php',
root/'wa-config/apps/shop/checkout2.php']]},ensure_ascii=False))
# checkout2 source: only public agreement text and metadata around keys, no secrets
q=root/'wa-config/apps/shop/checkout2.php'
if q.is_file():
 lines=q.read_text(encoding='utf-8',errors='replace').splitlines()
 result=[]
 for i,l in enumerate(lines):
  if 'service_agreement' in l or 'shipping_agreement' in l:
   result.append({'n':i+1,'key':l.strip()[:140],
     'neighbor_line_lengths':[len(t) for t in lines[i:i+5]],
     'agreement_text_snippets':[t.strip()[:210] for t in lines[i:i+5] if ('href=' in t or 'политик' in t.lower() or 'условия' in t.lower())]})
 print('HOMECHECK_AGREEMENTS='+json.dumps(result[:28],ensure_ascii=False))
PY
"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-stage17-homeaudit-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage17-homeaudit-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('HOMECHECK_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
