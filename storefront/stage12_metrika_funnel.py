#!/usr/bin/env python3
"""Stage 12: restore modern Metrica funnel goals without personal data.

Only the active head include and a new JS module are modified. Product, order,
category and stock records are untouched.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,base64,shutil,tempfile,subprocess,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
layout=theme/'index.html'
asset=theme/'js/metrika_funnel_v1.js'
old=layout.read_text(encoding='utf-8')
anchor='<script src="{$wa_real_theme_url}js/user.js?v{$wa_theme_version}"></script>'
js=base64.b64decode('FUNNEL_B64').decode('utf-8')
if old.count(anchor)!=1 or asset.exists() or 'metrika_funnel_v1.js' in old:
 raise RuntimeError('Storefront changed since audit, abort')
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
backup=backups/('stage12-funnel-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup.mkdir(mode=0o700)
shutil.copy2(str(layout),str(backup/'index.html'))
os.chmod(str(backup/'index.html'),0o600)
written=False;created=False
try:
 st=layout.stat()
 with tempfile.NamedTemporaryFile('w',dir=str(theme),encoding='utf-8',delete=False) as fp:
  fp.write(old.replace(anchor,anchor+'\n    <script src="{$wa_real_theme_url}js/metrika_funnel_v1.js?v=20261010a" defer></script>',1));tpl=fp.name
 with tempfile.NamedTemporaryFile('w',dir=str(asset.parent),encoding='utf-8',delete=False) as fp:
  fp.write(js);tmpjs=fp.name
 os.chown(tpl,st.st_uid,st.st_gid);os.chmod(tpl,st.st_mode)
 os.chown(tmpjs,st.st_uid,st.st_gid);os.chmod(tmpjs,0o644)
 os.replace(tmpjs,str(asset));created=True
 os.replace(tpl,str(layout));written=True
 checks=[]
 for path in ('/','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/order/'):
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','32','-w','\n%{http_code}','https://profikompany.ru'+path+'?stage12audit=1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  data,_=p.communicate(timeout=38)
  h,status=data.rsplit('\n',1) if '\n' in data else ('','')
  head=h.split('</head>')[0]
  ok=(p.returncode==0 and status=='200' and head.count('metrika_funnel_v1.js?v=20261010a')==1)
  if path=='/order/':ok=ok and 'noindex,follow' in head
  checks.append({'page':path,'status':status,'ok':ok})
 if not all(x['ok'] for x in checks):raise RuntimeError('Live page checks failed: '+json.dumps(checks))
 print('FUNNEL_APPLY='+json.dumps({'ok':True,'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as ex:
 if written:shutil.copy2(str(backup/'index.html'),str(layout))
 if created and asset.exists():asset.unlink()
 print('FUNNEL_ROLLBACK='+json.dumps({'reason':str(ex),'restored':written},ensure_ascii=False))
 raise
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
