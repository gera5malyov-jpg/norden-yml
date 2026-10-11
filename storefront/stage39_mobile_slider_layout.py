#!/usr/bin/env python3
"""Correct mobile slider intrinsic dimensions after successful WebP rollout.

Preserves optimized images and original PNG fallback; backed up with rollback.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.slider.html'
original=file.read_text(encoding='utf-8')
needle=''' width="2172" height="724" decoding="async"'''
if original.count(needle)!=1 or original.count('megapolis-webp-slider-2026')!=1:
 raise RuntimeError('Slider optimized source mismatch, no changes')
new=original.replace(needle,' decoding="async"',1)
backupdir=root.parent/'storefront-backups'/('stage39-mobile-slide-height-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backupdir.mkdir(mode=0o700,parents=True,exist_ok=True)
backup=backupdir/'layout.slider.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.stage39-',delete=False) as f:
  f.write(new);tmp=f.name
 os.chmod(tmp,st.st_mode&0o777);os.chown(tmp,st.st_uid,st.st_gid)
 os.replace(tmp,str(file));written=True
 tests=[]
 for route in ['/','/category/kompyuternye-kresla/','/order/']:
  p=subprocess.Popen(['curl','-ksSL','--max-time','30','-w','\n%{http_code}','https://profikompany.ru'+route+'?stage39='+str(int(time.time()))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,err=p.communicate(timeout=38)
  body,status=output.rsplit('\n',1) if '\n' in output else ('','')
  has_webp=('slider_01.webp' in body and body.count('srcset=')>=7)
  has_fixed_height=('width="2172" height="724"' in body)
  ok=(p.returncode==0 and status=='200' and (route!='/' or (has_webp and not has_fixed_height)))
  tests.append({'route':route,'http':status,'webp_active':has_webp,'explicit_size_removed':not has_fixed_height,'ok':ok})
 if not all(x['ok'] for x in tests):raise RuntimeError('Site smoke failed '+json.dumps(tests))
 print('STAGE39_APPLY='+json.dumps({'ok':True,'backup':str(backup),'checks':tests},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE39_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
 with tempfile.TemporaryDirectory(prefix='stage39-mobile-slide-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage39-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE39_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
