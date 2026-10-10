#!/usr/bin/env python3
"""Stage 22: lazy-load hidden category navigation icons to improve mobile conversion.

Only two navigation theme templates are touched. No product/category/order database writes.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
folder=root/'wa-data/public/site/themes/pureMegapolis42/components'
paths=[folder/'component.catalog.inline.html',folder/'component.catalog.html']
old={p:p.read_text(encoding='utf-8') for p in paths}
new={}
for p,txt in old.items():
 needle='<img src="{$_image}" alt='
 if txt.count(needle)!=2:raise RuntimeError('Menu image source changed in '+p.name)
 updated=txt.replace(needle,'<img loading="lazy" decoding="async" src="{$_image}" alt=')
 if updated.count('loading="lazy" decoding="async" src="{$_image}"')!=2:
  raise RuntimeError('Unexpected replacement count '+p.name)
 new[p]=updated
backup_root=root.parent/'storefront-backups'
backup_root.mkdir(parents=True,mode=0o700,exist_ok=True)
bdir=backup_root/('stage22-nav-lazy-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
bdir.mkdir(mode=0o700)
for p in paths:
 dest=bdir/p.name
 shutil.copy2(str(p),str(dest));os.chmod(str(dest),0o600)
written=[]
try:
 for p in paths:
  st=p.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(p.parent),prefix='.nav-lazy-',delete=False) as fp:
   fp.write(new[p]);temp=fp.name
  os.chown(temp,st.st_uid,st.st_gid);os.chmod(temp,st.st_mode)
  os.replace(temp,str(p));written.append(p)
 checks=[]
 for path in ('/','/category/kompyuternye-kresla/','/order/'):
  url='https://profikompany.ru'+path+'?lazy_nav_verify='+str(int(time.time()))
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','34','-w','\n%{http_code}',url],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  result,err=p.communicate(timeout=41)
  html,status=result.rsplit('\n',1) if '\n' in result else ('','')
  has=html.count('loading="lazy" decoding="async" src="/wa-data/public/shop/skcatimage/')>=20
  ok=p.returncode==0 and status=='200' and has
  checks.append({'path':path,'http':status,'lazy_category_icons':has,'ok':ok})
 if not all(x['ok'] for x in checks):raise RuntimeError('Category image smoke failed '+json.dumps(checks))
 print('STAGE22_APPLY='+json.dumps({'success':True,'files':[str(p.relative_to(root)) for p in written],'backup':str(bdir),'checks':checks},ensure_ascii=False))
except Exception as e:
 for p in written:shutil.copy2(str(bdir/p.name),str(p))
 print('STAGE22_ROLLBACK='+json.dumps({'reason':str(e),'restored':[str(p.relative_to(root)) for p in written]},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage22-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage22-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE22_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
