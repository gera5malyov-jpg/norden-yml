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
import os,json,pathlib,shutil,tempfile,subprocess,base64
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
layout=theme/'index.html'
asset=theme/'js/megapolis-ecommerce-v1.js'
old=layout.read_text(encoding='utf-8')
anchor='<script src="{$wa_real_theme_url}js/megapolis-funnel-v2.js?v=20261013a"></script>'
tag='<script src="{$wa_real_theme_url}js/megapolis-ecommerce-v1.js?v=20261018a" defer></script>'
js=base64.b64decode('ECOM_B64').decode('utf-8')
print('FUNNEL_STATE='+json.dumps({'oldV2':old.count(anchor),'oldEcomTag':old.count('megapolis-ecommerce-v1.js'),'oldV1Tag':old.count('metrika_funnel_v1.js')}),flush=True)
if old.count(anchor)!=1 or old.count('metrika_funnel_v1.js') or old.count('megapolis-ecommerce-v1.js')>1:
 raise RuntimeError('Unexpected storefront head, abort')
if asset.exists() and asset.read_text(encoding='utf-8')!=js:
 raise RuntimeError('Existing ecom module differs from reviewed source, abort')
if old.count(tag)==1 and asset.exists():
 print('FUNNEL_RESULT='+json.dumps({'status':'already_installed'}),flush=True)
 raise SystemExit(0)
if old.count('megapolis-ecommerce-v1.js')>0:
 raise RuntimeError('Unknown ecommerce asset already referenced, abort')
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
backup=backups/('metrica-ecom-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup.mkdir(mode=0o700)
shutil.copy2(str(layout),str(backup/'index.html'))
os.chmod(str(backup/'index.html'),0o600)
written=False
created=False
try:
 st=layout.stat()
 with tempfile.NamedTemporaryFile('w',dir=str(theme),encoding='utf-8',delete=False) as fp:
  fp.write(old.replace(anchor,anchor+'\n    '+tag,1))
  tpl=fp.name
 os.chown(tpl,st.st_uid,st.st_gid);os.chmod(tpl,st.st_mode)
 if not asset.exists():
  with tempfile.NamedTemporaryFile('w',dir=str(asset.parent),encoding='utf-8',delete=False) as fp:
   fp.write(js)
   js_tmp=fp.name
  os.chown(js_tmp,st.st_uid,st.st_gid);os.chmod(js_tmp,0o644)
  os.replace(js_tmp,str(asset));created=True
 os.replace(tpl,str(layout));written=True
 checks=[]
 for path in ('/','/stul-signal-misaki-t-253-bezhevyy-kashemir/','/order/','/category/kompyuternye-kresla/'):
  r=subprocess.run(['curl','-k','-sS','-L','--max-time','35','-w','\n%{http_code}','https://profikompany.ru'+path+'?ecom_deploy_check=20261018'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=40)
  html,status=r.stdout.rsplit('\n',1) if '\n' in r.stdout else ('','')
  head=html.split('</head>')[0]
  ok=(r.returncode==0 and status=='200' and head.count('megapolis-ecommerce-v1.js?v=20261018a')==1 and head.count('megapolis-funnel-v2.js')==1 and head.count('metrika_funnel_v1.js')==0 and head.count('ym(21811312, "init"')==1)
  checks.append({'path':path,'status':status,'ecom':head.count('megapolis-ecommerce-v1.js'),'v2':head.count('megapolis-funnel-v2.js'),'ok':ok})
 if not all(x['ok'] for x in checks):
  raise RuntimeError('HTML regressions '+json.dumps(checks))
 print('FUNNEL_RESULT='+json.dumps({'status':'installed','backup':str(backup),'checks':checks}),flush=True)
except Exception as e:
 if written:shutil.copy2(str(backup/'index.html'),str(layout))
 if created and asset.exists():asset.unlink()
 print('FUNNEL_ROLLBACK='+json.dumps({'reason':str(e),'restored':written}),flush=True)
 raise
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
            input=REMOTE.replace('ECOM_B64',base64.b64encode((Path(__file__).parent/'assets/megapolis-ecommerce-v1.js').read_bytes()).decode('ascii')),text=True,capture_output=True,timeout=160)
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
