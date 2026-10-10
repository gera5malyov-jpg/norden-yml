#!/usr/bin/env python3
"""Stage 23: make embedded Yandex map responsive on mobile homepage.

Touches one contact template only, backed up, domain scoped. No catalog/data edits.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.contact.html'
orig=file.read_text(encoding='utf-8')
if 'contact__map' not in orig or 'megapolis-responsive-contact-map' in orig:
 raise RuntimeError('Contact map source differs from audited theme')
styling="""
{* Prevent the Yandex map's fixed 500px iframe from widening the mobile homepage *}
{if $wa->domainUrl() == 'https://profikompany.ru'}
<style id="megapolis-responsive-contact-map">
@media (max-width:767px) {
  .contact__map iframe { width:100% !important; max-width:100%; display:block; }
}
</style>
{/if}
"""
edited=orig+'\n'+styling
broot=root.parent/'storefront-backups';broot.mkdir(mode=0o700,parents=True,exist_ok=True)
bdir=broot/('stage23-map-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
bdir.mkdir(mode=0o700);backup=bdir/'layout.contact.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.responsive-map-',delete=False) as fp:
  fp.write(edited);temp=fp.name
 os.chown(temp,st.st_uid,st.st_gid);os.chmod(temp,st.st_mode)
 os.replace(temp,str(file));written=True
 checks=[]
 for path in ('/','/order/'):
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','30','-w','\n%{http_code}',
    'https://profikompany.ru'+path+'?map_mobile_check='+str(int(time.time()))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=37)
  html,status=out.rsplit('\n',1) if '\n' in out else ('','')
  has_rule='id="megapolis-responsive-contact-map"' in html if path=='/' else True
  ok=p.returncode==0 and status=='200' and has_rule
  checks.append({'path':path,'http':status,'mobile_rule_present':has_rule,'ok':ok})
 if not all(x['ok'] for x in checks):raise RuntimeError('Live map smoke failed '+json.dumps(checks))
 print('STAGE23_APPLY='+json.dumps({'success':True,'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE23_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage23-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage23-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE23_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
