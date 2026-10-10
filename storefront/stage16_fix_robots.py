#!/usr/bin/env python3
"""Stage 16: safe Webasyst domain robots Sitemap declaration.

Only edits the profikompany domain robots configuration with automatic rollback.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,tempfile,subprocess,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru')
robots=root/'www/wa-data/public/site/data/profikompany.ru/robots.txt'
if not robots.is_file():raise RuntimeError('Domain-specific Webasyst robots.txt not found')
source=robots.read_text(encoding='utf-8')
expected='User-agent: *\n# wa shop *\nDisallow: /my/\nDisallow: /checkout/\n# wa shop\n# wa shop \nDisallow: /my/\nDisallow: /checkout/\n# wa shop\n\n'
if source.replace('\r\n','\n').replace('\r','\n')!=expected:
 raise RuntimeError('Current robots file differs from read-only audit; no changes')
updated=('User-agent: *\n'
         '# wa shop *\n'
         'Disallow: /my/\n'
         'Disallow: /checkout/\n'
         '# wa shop\n'
         '\n'
         'Sitemap: https://profikompany.ru/sitemap.xml\n')
if not updated.startswith('User-agent: *\n'):raise RuntimeError('Preflight failed')
backups=root/'storefront-backups'
backups.mkdir(parents=True,mode=0o700,exist_ok=True)
directory=backups/('stage16-robots-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
directory.mkdir(mode=0o700)
backup=directory/'robots.txt'
shutil.copy2(str(robots),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
 st=robots.stat()
 with tempfile.NamedTemporaryFile('w',dir=str(robots.parent),encoding='utf-8',prefix='.robots-',delete=False) as f:
  f.write(updated);temp=f.name
 os.chown(temp,st.st_uid,st.st_gid)
 os.chmod(temp,st.st_mode)
 os.replace(temp,str(robots));written=True
 p=subprocess.Popen(['curl','-sS','-L','--max-time','25','-w','\n%{http_code}',
      'https://profikompany.ru/robots.txt?robots_validation=1'],
      stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=31)
 body,status=out.rsplit('\n',1) if '\n' in out else ('','')
 ok=(p.returncode==0 and status=='200'
   and body.count('Sitemap: https://profikompany.ru/sitemap.xml')==1
   and body.count('Disallow: /checkout/')==1
   and body.count('Disallow: /my/')==1)
 if not ok:
  raise RuntimeError('Live robots check failed, status='+status+' preview='+repr(body[:210]))
 print('SEO_ROBOTS_APPLY='+json.dumps({'status':'success','backup':str(backup),
  'url':'https://profikompany.ru/robots.txt','http':status,
  'sitemap_declared':True,'duplicate_rules_removed':True},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(robots))
 print('SEO_ROBOTS_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage16-robots-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage16-robots-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('SEO_ROBOTS_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
