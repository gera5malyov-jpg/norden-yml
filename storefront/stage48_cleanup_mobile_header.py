#!/usr/bin/env python3
"""Clean mobile header: hide duplicate hamburger and header callback; keep both numbers.

Guarded edits of two active theme templates, server backups and rollback.
No database, product, order, pricing or marketplace writes.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/site/themes/pureMegapolis42'
component=theme/'components/component.phones.html'
header=theme/'header/header.top.html'
items=[component,header]
original={f:f.read_text(encoding='utf-8') for f in items}
callback_before='{if !empty($_company__callback_text)}'
callback_after='''{if !empty($_company__callback_text) && !($wa->domainUrl() == 'https://profikompany.ru' && isset($_phones_place) && $_phones_place == 'header-top')}'''
header_before='<div class="header-top is-loading header-top--'
newstyle='''{* megapolis-clean-mobile-header-20261011: keep two phones and a single mobile menu trigger *}
{if $wa->domainUrl() == 'https://profikompany.ru'}
{literal}
<style>
.header-top .phones--header-top .phones__content{
  display:flex!important;flex-flow:row wrap;align-items:center;column-gap:18px;row-gap:6px;
}
.header-top .phones--header-top .phones__link--phone{
  display:inline-block!important;white-space:nowrap;
}
@media (max-width:767px) {
  .header-top .header-top__item--mobile-nav{display:none!important;}
  .header-top .header-top__item--phones{min-width:0;max-width:100%;width:100%;padding-left:8px;padding-right:8px;box-sizing:border-box;}
  .header-top .phones--header-top{width:100%;}
  .header-top .phones--header-top .phones__content{
    display:flex!important;flex-flow:row wrap;justify-content:center;align-items:center;
    column-gap:14px;row-gap:6px;width:100%;text-align:center;
  }
  .header-top .phones--header-top .phones__link--phone{
    font-size:13px!important;line-height:1.45;letter-spacing:0;white-space:nowrap;
  }
}
</style>
{/literal}
{/if}
'''
if original[component].count(callback_before)!=1:
 raise RuntimeError('Callback condition unexpectedly changed')
if original[header].count(header_before)!=1 or 'megapolis-clean-mobile-header-20261011' in original[header]:
 raise RuntimeError('Header markup changed or patch already applied')
if 'megapolis-paired-phones-2026' not in original[component]:
 raise RuntimeError('Expected two-number header configuration absent; refuse update')
modified={
 component:original[component].replace(callback_before,callback_after,1),
 header:original[header].replace(header_before,newstyle+header_before,1)
}
for f in items:
 if modified[f]==original[f]:raise RuntimeError('No change produced in '+str(f))
base=root.parent/'storefront-backups'
base.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=base/('stage48-mobile-header-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
saved={}
for i,file in enumerate(items):
 backup=folder/('%02d-%s'%(i,file.name))
 shutil.copy2(str(file),str(backup))
 os.chmod(str(backup),0o600)
 saved[file]=backup
written=[]
try:
 for file in items:
  stat=file.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.header-stage48-',delete=False) as out:
   out.write(modified[file]);tmp=out.name
  os.chmod(tmp,stat.st_mode)
  os.chown(tmp,stat.st_uid,stat.st_gid)
  os.replace(tmp,str(file))
  written.append(file)
 tests=[]
 for path in ('/','/category/kompyuternye-kresla/','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/order/','/proizvoditeli/'):
  p=subprocess.Popen(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',
      'https://profikompany.ru'+path+'?stage48_test='+str(os.getpid())],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  html,err=p.communicate(timeout=42)
  body,status=html.rsplit('\n',1) if '\n' in html else ('','')
  header_html=body.split('<header class="header',1)[-1].split('header-middle',1)[0]
  phone_links=header_html.count('tel:+7(812)2449264')+header_html.count('tel:+7(499)6776332')
  callback_header='phones__link--callback' in header_html
  ok=(p.returncode==0 and status=='200' and
   not callback_header and phone_links>=2 and
   'megapolis-clean-mobile-header-20261011' not in body and
   '.header-top .header-top__item--mobile-nav{display:none!important;}' in body and
   'class="categories-button btn btn-default"' in body and
   'class="mobile-nav-toggle icon-burger"' in body)
  tests.append({'path':path,'http':status,'header_phone_links':phone_links,'callback_in_header':callback_header,'ok':ok})
 if not all(x['ok'] for x in tests):
  raise RuntimeError('Live smoke failed '+json.dumps(tests,ensure_ascii=False))
 print('STAGE48_APPLY='+json.dumps({'ok':True,'backup':str(folder),'modified':[str(f.relative_to(root)) for f in written],'checks':tests},ensure_ascii=False))
except Exception as e:
 for file in reversed(written):
  shutil.copy2(str(saved[file]),str(file))
 print('STAGE48_ROLLBACK='+json.dumps({'error':str(e),'restored':[str(f.relative_to(root)) for f in written]},ensure_ascii=False))
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
    if line.startswith('STAGE48_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact deployment failed '+p.stdout[-2000:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
