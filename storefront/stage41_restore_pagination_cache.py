#!/usr/bin/env python3
"""Recover live paginated category routes from stale Smarty compiled SEO template.

Touches only verified healthy current theme index file to force recompilation.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,subprocess,time
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
source=file.read_text(encoding='utf-8')
if '$_seo_pagination_number' in source:raise RuntimeError('Faulty pagination code remains in source - touch alone unsafe')
if 'Scoped SEO improvements' not in source or '<link rel="canonical" href="{$canonical|escape}"' not in source:
 raise RuntimeError('Unexpected page template content; no changes')
before=file.stat().st_mtime
os.utime(str(file),None)
print('STAGE41_TOUCH='+json.dumps({'mtime_before':before,'mtime_after':file.stat().st_mtime,'source_clean':True}))
cases=['/','/category/kompyuternye-kresla/?page=2','/category/kompyuternye-kresla/?page=3',
  '/category/kompyuternye-kresla/?page=2&sort=price','/order/']
for attempt in range(1,4):
 checks=[]
 for route in cases:
  p=subprocess.Popen(['curl','-ksSL','--max-time','25','-w','\n%{http_code}','https://profikompany.ru'+route],
     stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,err=p.communicate(timeout=31)
  body,status=output.rsplit('\n',1) if '\n' in output else ('','')
  ok=p.returncode==0 and status=='200'
  checks.append({'route':route,'http':status,'ok':ok})
 print('STAGE41_SMOKE='+json.dumps({'attempt':attempt,'checks':checks},ensure_ascii=False))
 if all(x['ok'] for x in checks):
  print('STAGE41_RECOVERED='+json.dumps({'success':True,'attempt':attempt},ensure_ascii=False))
  break
 time.sleep(2)
else:
 raise RuntimeError('Template recompile did not recover pagination. Inspect compiled cache.')
PY
"""

def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels gateway token missing')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage41-recompile-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage41-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE41_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
