#!/usr/bin/env python3
"""Restrict tablet contact map iframe width to column without modifying content.

Guarded single template styling with server backup and rollback.
No database, product, order, pricing or marketplace writes.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.contact.html'
before=file.read_text(encoding='utf-8')
if not before.lstrip().startswith('{strip}') or 'megapolis-contact-map-responsive-2026' in before:
 raise RuntimeError('Contact layout unexpected or patch already exists; no changes')
style='''{* megapolis-contact-map-responsive-2026: prevent 500px iframe on tablet from widening page *}
{if $wa->domainUrl() == 'https://profikompany.ru'}
{literal}
<style>
@media (min-width:768px) and (max-width:991px) {
  .contact__map iframe {width:100%!important;max-width:100%!important;}
}
</style>
{/literal}
{/if}
'''
updated=before.replace('{strip}','{strip}\n'+style,1)
backuproot=root.parent/'storefront-backups'
backuproot.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backuproot/('stage51-tablet-map-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backup=folder/'layout.contact.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.stage51-map-',delete=False) as t:
  t.write(updated);temp=t.name
 os.chmod(temp,st.st_mode);os.chown(temp,st.st_uid,st.st_gid)
 os.replace(temp,str(file));written=True
 results=[]
 for path in ('/','/category/kompyuternye-kresla/','/order/'):
  p=subprocess.Popen(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',
    'https://profikompany.ru'+path+'?stage51_check='+str(os.getpid())],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=42)
  body,status=out.rsplit('\n',1) if '\n' in out else ('','')
  style_found='max-width:100%!important' in body if path=='/' else True
  ok=p.returncode==0 and status=='200' and style_found
  results.append({'path':path,'http':status,'responsive_map_rule':style_found,'ok':ok})
 if not all(t['ok'] for t in results):raise RuntimeError('Map responsive smoke failed '+json.dumps(results))
 print('STAGE51_APPLY='+json.dumps({'ok':True,'backup':str(backup),'checks':results},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE51_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
 raise
PY
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage25-contact-deploy-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage25-fix-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=190)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE51_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact deployment failed '+p.stdout[-2000:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
