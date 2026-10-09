#!/usr/bin/env python3
"""One-shot footer repair: replace dead payment/delivery menu links only."""
from __future__ import annotations
import os,json,subprocess,tempfile,urllib.parse
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r'''set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
f=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html'
original=f.read_text(encoding='utf-8')
lines=original.splitlines(True)
specs={'Terms of payment':'/oplata/','Terms of delivery':'/dostavka/'}
for label,target in specs.items():
  indices=[i for i,line in enumerate(lines) if label in line]
  if len(indices)!=1:raise RuntimeError('Footer link label mismatch for '+label)
  i=indices[0]
  if lines[i].count('href="#"')!=1:raise RuntimeError('Footer link source mismatch for '+label)
  lines[i]=lines[i].replace('href="#"','href="'+target+'"',1)
edited=''.join(lines)
backup_base=root.parent/'storefront-backups'
backup_base.mkdir(mode=0o700,parents=True,exist_ok=True)
backup_dir=backup_base/('stage3-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup_dir.mkdir(mode=0o700)
backup=backup_dir/'layout.footer.html'
shutil.copy2(str(f),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
  st=f.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),prefix='.storefront-footer-',delete=False) as fp:
    fp.write(edited);temp=fp.name
  os.chown(temp,st.st_uid,st.st_gid);os.chmod(temp,st.st_mode)
  os.replace(temp,str(f));written=True
  checks=[]
  for path in ('','order/'):
    u='https://profikompany.ru/'+path+'?footer_verify='+str(int(time.time()))
    cmd=['curl','-k','-sS','-L','--max-time','35','-w','\n%{http_code}',u]
    p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    out,err=p.communicate(timeout=42)
    page,status=out.rsplit('\n',1) if '\n' in out else ('','')
    ok=p.returncode==0 and status=='200' and 'href="/oplata/" class="footer__link"' in page and 'href="/dostavka/" class="footer__link"' in page
    checks.append({'page':path or '/', 'http_status':status,'correct_links':ok})
  if not all(x['correct_links'] for x in checks):
    raise RuntimeError('Footer live checks failed: '+json.dumps(checks,ensure_ascii=False))
  print('STOREFRONT_STAGE3_APPLY='+json.dumps({'status':'success','file':str(f.relative_to(root)),
    'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as e:
  if written:shutil.copy2(str(backup),str(f))
  print('STOREFRONT_STAGE3_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
  raise
PY
'''

def run():
  api_key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not api_key:raise RuntimeError('NETANGELS_API_KEY unavailable')
  gateway=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
    urllib.parse.urlencode({'api_key':api_key}).encode(),
    {'Content-Type':'application/x-www-form-urlencoded'})
  token=gateway.get('token')
  if not token:raise RuntimeError('NetAngels gateway inaccessible')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-footer-') as d:
    key=d+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key],check=True)
    pub=open(key+'.pub',encoding='utf-8').read().strip()
    data=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',
      json.dumps({'key':pub,'name':'storefront-footer-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    key_id=data.get('id')
    if not key_id:raise RuntimeError('Short-lived SSH key could not be registered')
    try:
      p=subprocess.run(['ssh','-i',key,'-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null',
        '-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
        input=REMOTE,text=True,capture_output=True,timeout=170)
      for line in p.stdout.splitlines():
        if line.startswith('STOREFRONT_STAGE3_'):print(line,flush=True)
      if p.returncode:raise RuntimeError('Footer deploy failed: '+p.stderr[-800:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,key_id),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true')
      except Exception:print('WARNING: SSH key cleanup needs review')

if __name__=='__main__':run()
