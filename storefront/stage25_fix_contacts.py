#!/usr/bin/env python3
"""Fix the misleading callback label for profikompany.ru only.

Exact guarded edits of two active theme templates, server backups and rollback.
No database, product, order, pricing or marketplace writes.
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
theme=root/'wa-data/public/site/themes/pureMegapolis42'
targets=[theme/'layouts/layout.footer.html',theme/'components/component.phones.html']
old='{$_company__callback_text = $theme_settings.company__callback_text}'
addon="""
{* megapolis-callback-label-2026: the callback is a form, not the Moscow phone number *}
{if $wa->domainUrl() == 'https://profikompany.ru' && $_company__callback_text == '/+7 (499) 677 63 32'}
  {$_company__callback_text = 'Заказать звонок'}
{/if}"""
files={}
for file in targets:
 orig=file.read_text(encoding='utf-8')
 if 'megapolis-callback-label-2026' in orig:
  raise RuntimeError('Contact theme was already modified: '+str(file))
 if orig.count(old)!=1:raise RuntimeError('Unexpected callback source count in '+str(file))
 if '$_company__callback_link' not in orig or 'callback' not in orig:
  raise RuntimeError('Callback anchor unexpectedly missing: '+str(file))
 files[file]=(orig,orig.replace(old,old+'\\n'+addon,1))
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backups/('stage25-callback-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
for file in targets:
 dest=folder/file.name
 shutil.copy2(str(file),str(dest));os.chmod(str(dest),0o600)
written=[]
try:
 for file,(orig,edited) in files.items():
  st=file.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.callback-safe-',delete=False) as f:
   f.write(edited);tmp=f.name
  os.chown(tmp,st.st_uid,st.st_gid)
  os.chmod(tmp,st.st_mode)
  os.replace(tmp,str(file));written.append(file)
 checks=[]
 for urlpath in ('/','/order/','/proizvoditeli/'):
  u='https://profikompany.ru'+urlpath+'?stage25_check='+str(int(time.time()))
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','30','-w','\\n%{http_code}',u],
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,error=p.communicate(timeout=38)
  html,status=output.rsplit('\\n',1) if '\\n' in output else ('','')
  ok=(p.returncode==0 and status=='200' and '/+7 (499) 677 63 32' not in html
    and html.count('>Заказать звонок</a>')>=3
    and 'tel:+7(499)6776332' in html and 'mailto:shop@office-mag.com' in html)
  checks.append({'route':urlpath,'http':status,'callback_labels':html.count('>Заказать звонок</a>'),
                 'old_malformed_label':('/+7 (499) 677 63 32' in html),'ok':ok})
 if not all(c['ok'] for c in checks):raise RuntimeError('Live callback verification failed '+json.dumps(checks))
 print('STAGE25_APPLY='+json.dumps({'ok':True,'backups':str(folder),'updated':[str(f.relative_to(root)) for f in written],'checks':checks},ensure_ascii=False))
except Exception as e:
 for file in written:shutil.copy2(str(folder/file.name),str(file))
 print('STAGE25_ROLLBACK='+json.dumps({'error':str(e),'restored':[str(f.name) for f in written]},ensure_ascii=False))
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
    if line.startswith('STAGE25_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact deployment failed '+p.stdout[-2000:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
