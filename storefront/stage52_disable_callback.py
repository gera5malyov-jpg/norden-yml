#!/usr/bin/env python3
"""Disable callback output only on profikompany.ru; guard, back up, smoke and roll back.
No product/category/price/stock/order database writes.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse,sys
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE_DEPLOY=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
targets=[
 (theme/'index.html',
 "{* CALLBACK MODAL *}\n{if $theme_settings.main__easyfeedback == 'default'}",
 "{* CALLBACK MODAL *}\n{if $wa->domainUrl() != 'https://profikompany.ru'}\n{if $theme_settings.main__easyfeedback == 'default'}"),
 (theme/'layouts/layout.footer.html',
 "{if !empty($_company__callback_text) && !empty($theme_settings.footer__callback)}",
 "{if !empty($_company__callback_text) && !empty($theme_settings.footer__callback) && $wa->domainUrl() != 'https://profikompany.ru'}"),
 (theme/'components/component.phones.html',
 "{if !empty($_company__callback_text) && !($wa->domainUrl() == 'https://profikompany.ru' && isset($_phones_place) && $_phones_place == 'header-top')}",
 "{if !empty($_company__callback_text) && $wa->domainUrl() != 'https://profikompany.ru'}")
]
original={}
updated={}
for f,old,new in targets:
 s=f.read_text(encoding='utf-8')
 if s.count(old)!=1 or new in s:
  raise RuntimeError('Guard mismatch or patch already applied: '+str(f.relative_to(root)))
 if f.name=='index.html' and s.count('id="callback-modal"')!=1:
  raise RuntimeError('Main modal changed, refusing to patch')
 if f.name=='component.phones.html' and 'megapolis-paired-phones-2026' not in s:
  raise RuntimeError('Two-phone block absent; refusing to patch')
 original[f]=s
 updated[f]=s.replace(old,new,1)
 if f.name=='index.html':
  ending_old='{/if}\n\n{* FLY CART *}'
  ending_new='{/if}\n{/if}\n\n{* FLY CART *}'
  if s.count(ending_old)!=1 or "{elseif $theme_settings.main__easyfeedback == 'dialog'}" not in s:
   raise RuntimeError('Unexpected callback modal closing block')
  updated[f]=updated[f].replace(ending_old,ending_new,1)
print('STAGE52_PREFLIGHT='+json.dumps({'files':[str(f.relative_to(root)) for f in original]},ensure_ascii=False),flush=True)
base=root.parent/'storefront-backups'
base.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=base/('stage52-no-callback-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
names=('00-index.html','01-layout.footer.html','02-component.phones.html')
backups={}
for (f,_,_),name in zip(targets,names):
 backup=folder/name
 shutil.copy2(str(f),str(backup));os.chmod(str(backup),0o600)
 backups[f]=backup
written=[]
try:
 for f,old,new in targets:
  stat=f.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),prefix='.stage52-',delete=False) as temp:
   temp.write(updated[f]);tmp=temp.name
  os.chmod(tmp,stat.st_mode);os.chown(tmp,stat.st_uid,stat.st_gid)
  os.replace(tmp,str(f));written.append(f)
 tests=[]
 for route in ('/','/order/','/garantiya/','/category/kompyuternye-kresla/','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'):
  p=subprocess.run(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',
   'https://profikompany.ru'+route+'?stage52_verify='+str(os.getpid())],
   capture_output=True,text=True,timeout=42)
  html,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
  visible_call='Заказать звонок' in html or 'phones__link--callback' in html or 'id="callback-modal"' in html
  spb='tel:+7(812)2449264' in html
  msk='tel:+7(499)6776332' in html
  ok=p.returncode==0 and status=='200' and not visible_call and spb and msk
  if route.endswith('chernaya/'):
   ok=ok and 'class="product-add"' in html and 'id="megapolis-mobile-buy"' in html
  tests.append({'route':route,'status':status,'spb':spb,'msk':msk,'callback_visible':visible_call,'ok':ok})
 if not all(x['ok'] for x in tests):
  raise RuntimeError('Post-patch smoke failed '+json.dumps(tests,ensure_ascii=False))
 print('STAGE52_DEPLOY='+json.dumps({'success':True,'backup':str(folder),'files':[str(f.relative_to(root)) for f in written],'checks':tests},ensure_ascii=False),flush=True)
except Exception as e:
 for f in reversed(written):
  shutil.copy2(str(backups[f]),str(f))
 print('STAGE52_ROLLBACK='+json.dumps({'reason':str(e),'files_restored':[str(f.relative_to(root)) for f in written]},ensure_ascii=False),flush=True)
 raise
PY

"""

def remote_rollback(backup_folder):
 if not backup_folder.startswith('/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage52-no-callback-'):
  raise RuntimeError('Invalid stage52 backup path')
 return """set -eu
python3 - <<'PY'
import json,pathlib,shutil
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
folder=pathlib.Path("""+json.dumps(backup_folder)+""")
targets=[
 (theme/'index.html',folder/'00-index.html',"{if $wa->domainUrl() != 'https://profikompany.ru'}"),
 (theme/'layouts/layout.footer.html',folder/'01-layout.footer.html',"{if !empty($_company__callback_text) && !empty($theme_settings.footer__callback) && $wa->domainUrl() != 'https://profikompany.ru'}"),
 (theme/'components/component.phones.html',folder/'02-component.phones.html',"{if !empty($_company__callback_text) && $wa->domainUrl() != 'https://profikompany.ru'}")
]
for file,back,guard in targets:
 if not back.is_file():raise RuntimeError('Rollback backup missing')
 if guard not in file.read_text(encoding='utf-8'):raise RuntimeError('Live template changed, refusing unsafe rollback')
for file,back,guard in targets:shutil.copy2(str(back),str(file))
print('STAGE52_ROLLBACK='+json.dumps({'ok':True,'restored':[str(file.relative_to(root)) for file,_,_ in targets]},ensure_ascii=False))
PY
"""

def execute(remote):
 secret=os.environ.get('NETANGELS_API_KEY','').strip()
 if not secret:raise RuntimeError('Missing NetAngels API key')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
  urllib.parse.urlencode({'api_key':secret}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('Gateway access unavailable')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage52-callback-') as d:
  ssh=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
   'POST',json.dumps({'key':pub,'name':'storefront-stage52-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
  kid=row.get('id')
  if not kid:raise RuntimeError('NetAngels temporary SSH key unavailable')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
    '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
    input=remote,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE52_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Remote operation failed '+p.stdout[-2500:]+' '+p.stderr[-650:])
   return p.stdout
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review',flush=True)

def main():
 state=Path('stage52-state.json')
 if '--rollback' in sys.argv:
  if not state.is_file():
   print('No stage52 saved deployment state; no rollback needed')
   return
  data=json.loads(state.read_text())
  execute(remote_rollback(data['backup']))
  state.unlink()
  return
 result=execute(REMOTE_DEPLOY)
 deployed=None
 for line in result.splitlines():
  if line.startswith('STAGE52_DEPLOY='):
   deployed=json.loads(line.split('=',1)[1])
 if not deployed or not deployed.get('success'):
  raise RuntimeError('Stage52 live deploy result could not be verified')
 state.write_text(json.dumps({'backup':deployed['backup']}))
if __name__=='__main__':main()
