#!/usr/bin/env python3
"""Stage 21: update footer menu per user-specified list without changing data.

Only modifies the active theme footer; original backed up and restored on failures.
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
file=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html'
orig=file.read_text(encoding='utf-8')
# Stage 21 pages were already published by a separate workflow. Do not fail or
# rewrite the footer when the desired state is present.
ready_links=(
 'href="/o-kompanii/"','href="/oplata/"','href="/dostavka/"',
 'href="/privacy-policy/"','href="/proizvoditeli/"',
 'href="/garantiya/"','href="/vozmozhnosti/"'
)
obsolete=('News','Employees','Jobs','Our shops','Help','Articles','FAQ')
ready=(all(orig.count(link)==1 for link in ready_links)
 and 'href="#" class="footer__link"' not in orig
 and not any('['+chr(96)+name+chr(96)+']' in orig for name in obsolete)
 and orig.count('footer__title')==3)
if ready:
 checks=[]
 for path in ('/','/proizvoditeli/','/garantiya/','/vozmozhnosti/','/order/'):
  u='https://profikompany.ru'+path+'?footer_readonly_check='+str(int(time.time()))
  p=subprocess.Popen(['curl','-k','-sSL','--max-time','30','-w','\n%{http_code}',u],
                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,error=p.communicate(timeout=36)
  html,status=output.rsplit('\n',1) if '\n' in output else ('','')
  has_links=all(link in html for link in ready_links)
  good=p.returncode==0 and status=='200' and has_links
  checks.append({'path':path,'http':status,'footer_links':has_links,'ok':good})
 if not all(c['ok'] for c in checks):
  raise RuntimeError('Live footer verification failed: '+json.dumps(checks,ensure_ascii=False))
 print('STAGE21_ALREADY_CLEAN='+json.dumps({'status':'verified','writes':0,'checks':checks},ensure_ascii=False))
 raise SystemExit(0)
lines=orig.splitlines(True)
remove_labels=('News','Employees','Jobs','Our shops','Help','Articles','FAQ')
needed=('href="/o-kompanii/"','href="/oplata/"','href="/dostavka/"','href="/privacy-policy/"')
for needle in needed:
 if orig.count(needle)!=1:raise RuntimeError('Required footer link changed: '+needle)
new=[];removed={};replaced={}
for line in lines:
 if 'footer__list-item' in line and 'href="#"' in line:
  label=next((v for v in remove_labels if v in line),None)
  if label:
   removed[label]=removed.get(label,0)+1
   continue
 for label,path in (('Warranty','/garantiya/'),('Capabilities','/vozmozhnosti/'),('Manufacturers','/proizvoditeli/')):
  if label in line and 'footer__list-item' in line:
   if line.count('href="#"')!=1:raise RuntimeError('Footer link is not placeholder: '+label)
   line=line.replace('href="#"','href="'+path+'"',1)
   replaced[label]=replaced.get(label,0)+1
 if '<div class="footer__title">' in line and 'Help' in line:
  line=line.replace('['+chr(96)+'Help'+chr(96)+']','Каталог')
  replaced['Help heading']=replaced.get('Help heading',0)+1
 new.append(line)
for label in remove_labels:
 if removed.get(label)!=1:raise RuntimeError('Expected exactly one removable link '+label)
for label in ('Warranty','Capabilities','Manufacturers','Help heading'):
 if replaced.get(label)!=1:raise RuntimeError('Expected exactly one new link/heading '+label)
edited=''.join(new)
for v in ('href="/garantiya/"','href="/vozmozhnosti/"','href="/proizvoditeli/"'):
 if edited.count(v)!=1:raise RuntimeError('Footer target count mismatch '+v)
if edited.count('href="#"')!=0:raise RuntimeError('Unexpected blank footer links remain')
base=root.parent/'storefront-backups';base.mkdir(mode=0o700,parents=True,exist_ok=True)
bd=base/('stage21-footer-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
bd.mkdir(mode=0o700);backup=bd/'layout.footer.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.footer-links-',delete=False) as fp:
  fp.write(edited);temp=fp.name
 os.chown(temp,st.st_uid,st.st_gid);os.chmod(temp,st.st_mode)
 os.replace(temp,str(file));written=True
 checks=[]
 for path in ('/','/order/','/proizvoditeli/','/garantiya/','/vozmozhnosti/'):
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','25','-w','\n%{http_code}',
    'https://profikompany.ru'+path+'?footer_v2_check='+str(int(time.time()))],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,error=p.communicate(timeout=32)
  html,status=output.rsplit('\n',1) if '\n' in output else ('','')
  has_footer=all(x in html for x in ('href="/proizvoditeli/"','href="/garantiya/"','href="/vozmozhnosti/"'))
  ok=(p.returncode==0 and status=='200' and has_footer)
  checks.append({'path':path,'http':status,'footer_links':has_footer,'ok':ok})
 if not all(x['ok'] for x in checks):
  raise RuntimeError('Footer live smoke failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE21_APPLY='+json.dumps({'status':'success','removed':removed,'links':replaced,'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as ex:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE21_ROLLBACK='+json.dumps({'reason':str(ex),'restored':written},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage21-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage21-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE21_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
