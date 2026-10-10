#!/usr/bin/env python3
"""Stage 20: fix footer email link only, preserving existing footer menu.

Only domain-scoped footer email markup is modified, with backup and rollback.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,tempfile,shutil,subprocess,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html'
orig=file.read_text(encoding='utf-8')
old='''          {if !empty($_company__address)}
            <li class="footer__list-item" itemprop="address">{$_company__address}</li>
          {/if}'''
new='''          {if !empty($_company__address)}
            {if $wa->domainUrl() == 'https://profikompany.ru' && $_company__address == 'shop@office-mag.com'}
              <li class="footer__list-item" itemprop="email"><a class="footer__link" href="mailto:shop@office-mag.com">shop@office-mag.com</a></li>
            {else}
              <li class="footer__list-item" itemprop="address">{$_company__address}</li>
            {/if}
          {/if}'''
if orig.count(old)!=1 or 'itemprop="email"' in orig:
 raise RuntimeError('Expected footer contact block changed - no writes')
changed=orig.replace(old,new,1)
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
directory=backups/('stage20-footer-email-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
directory.mkdir(mode=0o700)
backup=directory/'layout.footer.html'
shutil.copy2(str(file),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
 stat=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.footer-email-',delete=False) as fp:
  fp.write(changed);tmp=fp.name
 os.chmod(tmp,stat.st_mode);os.chown(tmp,stat.st_uid,stat.st_gid)
 os.replace(tmp,str(file));written=True
 checks=[]
 for path in ('/','/order/'):
  url='https://profikompany.ru'+path+'?stage20verify='+str(int(time.time()))
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','30','-w','\n%{http_code}',url],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=38)
  html,status=out.rsplit('\n',1) if '\n' in out else ('','')
  mark='itemprop="email"><a class="footer__link" href="mailto:shop@office-mag.com"'
  present=mark in html
  ok=p.returncode==0 and status=='200' and present and 'href="/oplata/"' in html and 'href="/dostavka/"' in html
  checks.append({'path':path,'http':status,'clickable_email':present,'ok':ok})
 if not all(c['ok'] for c in checks):raise RuntimeError('Footer smoke checks failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE20_APPLY='+json.dumps({'ok':True,'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE20_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage20-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage20-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE20_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
