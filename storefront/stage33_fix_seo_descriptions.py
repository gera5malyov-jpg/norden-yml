#!/usr/bin/env python3
"""Guarded, scoped storefront SEO meta-description cleanup (no catalog/DB edits).

Fixes misleading home catalogue size and category double-period typo.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time,re
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
path=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
original=path.read_text(encoding='utf-8')
anchor='''    {if !empty($_info_description)}
      <meta name="description" content="{$_info_description|escape}" />
    {else}
      <meta name="description" content="{$wa->meta('description')|escape}" />
    {/if}'''
homepage='''Мебель и предметы интерьера для дома и офиса: кресла, стулья, столы, диваны, шкафы и зеркала. Выбор мебели в Санкт-Петербурге с доставкой по России.'''
replacement='''    {if !empty($_info_description)}
      <meta name="description" content="{$_info_description|escape}" />
    {else}
      {* Scoped SEO improvements, no modifications to categories/products in Shop-Script *}
      {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->currentUrl(false, true) == '/'}
        <meta name="description" content="'''+homepage+'''" />
      {elseif $wa->domainUrl() == 'https://profikompany.ru' && $wa->param('action') == 'category'}
        <meta name="description" content="{$wa->meta('description')|replace:'руб..':'руб.'|escape}" />
      {else}
        <meta name="description" content="{$wa->meta('description')|escape}" />
      {/if}
    {/if}'''
if original.count(anchor)!=1 or 'Scoped SEO improvements' in original:
 raise RuntimeError('Theme SEO source changed, no writes')
updated=original.replace(anchor,replacement,1)
backup_root=root.parent/'storefront-backups'
backup_root.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backup_root/('stage33-seo-meta-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backup=folder/'index.html'
shutil.copy2(str(path),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=path.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(path.parent),prefix='.stage33-seo-',delete=False) as f:
  f.write(updated);tmp=f.name
 os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,st.st_mode)
 os.replace(tmp,str(path));written=True
 checks=[]
 for route in ('/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/kresla-rukovoditelya/','/category/myagkaya-mebel/divany/','/garantiya/'):
  u='https://profikompany.ru'+route+'?stage33_verify='+str(int(time.time()))
  p=subprocess.Popen(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',u],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,err=p.communicate(timeout=42)
  html,status=output.rsplit('\n',1) if '\n' in output else ('','')
  head=html.split('</head>',1)[0]
  mo=re.search(r'<meta name="description" content="([^"]*)"',head)
  description=mo.group(1) if mo else ''
  if route=='/':
   ok=(p.returncode==0 and status=='200' and description==homepage and head.count('name="description"')==1)
  elif route.startswith('/category/'):
   ok=(p.returncode==0 and status=='200' and 'руб..' not in description and description!='' and
     head.count('name="description"')==1 and 'rel="canonical"' in head)
  else:
   ok=(p.returncode==0 and status=='200' and 'Гарантийные обращения' in description)
  checks.append({'path':route,'http':status,'seo_description':description[:175],'ok':ok})
 if not all(x['ok'] for x in checks):
  raise RuntimeError('Live storefront smoke failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE33_APPLY='+json.dumps({'ok':True,'backup':str(backup),'file':str(path.relative_to(root)),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(path))
 print('STAGE33_ROLLBACK='+json.dumps({'error':str(e),'restored':written},ensure_ascii=False))
 raise
PY
"""

def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels gateway token missing')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage33-seo-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage33-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE33_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
