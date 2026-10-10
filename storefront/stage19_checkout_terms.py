#!/usr/bin/env python3
"""Stage 19: safely replace old demo checkout terms text in active store only.

Changes only one active checkout2.terms_text field with backup and automatic rollback.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse, base64
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile,base64,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
config=root/'wa-config/apps/shop/checkout2.php'
source=config.read_text(encoding='utf-8')
text=base64.b64decode('TERMS_B64_REPLACE_ME').decode('utf-8')
prefix="'terms_text' => '"
matches=[]
start_pos=0
while True:
 j=source.find(prefix,start_pos)
 if j<0:break
 k=j+len(prefix)
 if source[k:].startswith('<h1>УСЛОВИЯ ИНТЕРНЕТ-МАГАЗИНА</h1>'):
  matches.append((j,k))
 start_pos=j+len(prefix)
if len(matches)!=1:raise RuntimeError('Expected one old terms body in checkout2 config, found '+str(len(matches)))
marker_pos,value_start=matches[0]
# Strict domain guard: prior audited checkout settings contain profikompany privacy consent.
lookback=source[max(0,marker_pos-3500):marker_pos]
if 'href="/privacy-policy/"' not in lookback or 'service_agreement_hint' not in lookback:
 raise RuntimeError('Target checkout belongs to unexpected store route')
j=value_start
while j<len(source):
 if source[j]=='\\\\':
  j+=2;continue
 if source[j]=="'":break
 j+=1
if j>=len(source) or source[j+1:j+2]!=',':
 raise RuntimeError('Could not safely parse closing PHP single-quoted terms value')
old_terms=source[value_start:j]
if len(old_terms)<2000 or 'http://***' not in old_terms:
 raise RuntimeError('Current terms no longer match the old demo source')
if '<h2>Условия продажи товаров' not in text or '7838104843' not in text or '/privacy-policy/' not in text:
 raise RuntimeError('New terms document failed validation')
encoded=text.replace('\\\\','\\\\\\\\').replace("'", "\\\\'")
updated=source[:value_start]+encoded+source[j:]
if updated.count('УСЛОВИЯ ИНТЕРНЕТ-МАГАЗИНА') or updated.count('http://***')!=source.count('http://***')-1:
 raise RuntimeError('Terms update could not be isolated')
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backups/('stage19-terms-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backup=folder/'checkout2.php'
shutil.copy2(str(config),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
 st=config.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(config.parent),prefix='.checkout2-terms-',delete=False) as f:
  f.write(updated);temp=f.name
 os.chmod(temp,st.st_mode);os.chown(temp,st.st_uid,st.st_gid)
 p=subprocess.Popen(['php','-l',temp],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=25)
 if p.returncode:
  os.unlink(temp)
  raise RuntimeError('New checkout configuration failed PHP syntax validation')
 os.replace(temp,str(config));written=True
 if config.read_text(encoding='utf-8')!=updated:
  raise RuntimeError('Edited checkout configuration readback failed')
 checks=[]
 for path in ('/order/','/'):
  url='https://profikompany.ru'+path+'?stage19verify='+str(int(time.time()))
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','30','-w','\n%{http_code}',url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=38)
  html,status=out.rsplit('\n',1) if '\n' in out else ('','')
  valid=(p.returncode==0 and status=='200')
  checks.append({'path':path,'http':status,'ok':valid})
 if not all(c['ok'] for c in checks):
  raise RuntimeError('Checkout smoke test failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE19_APPLY='+json.dumps({'ok':True,'backup':str(backup),'file':str(config.relative_to(root)),
    'old_terms_length':len(old_terms),'new_terms_length':len(text),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(config))
 print('STAGE19_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage19-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage19-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE.replace('TERMS_B64_REPLACE_ME',base64.b64encode((Path(__file__).parent/'terms_profikompany_2026-10-10.html').read_bytes()).decode('ascii')),text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE19_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
