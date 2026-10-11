#!/usr/bin/env python3
"""Set self canonical for numbered category pages with only page parameter and unique products.

Retain original canonical on sort and filter pages; verify and rollback if needed.
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
file=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
old=file.read_text(encoding='utf-8')
anchor='''    {* CANONICAL *}
    {if !empty($canonical)}
      <link rel="canonical" href="{$canonical|escape}" />
    {/if}'''
replace='''    {* CANONICAL *}
    {* Index ordinary numbered category pages separately: their products differ;
       retain Shop-Script canonical for sorting/filter variations. *}
    {$_seo_pagination_number = $wa->get('page', 0)}
    {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->param('action') == 'category' && $_seo_pagination_number > 1 && count($wa->get()) == 1}
      <link rel="canonical" href="{$wa->currentUrl(true, true)|escape}?page={$_seo_pagination_number|intval}" />
    {elseif !empty($canonical)}
      <link rel="canonical" href="{$canonical|escape}" />
    {/if}'''
if old.count(anchor)!=1 or '$_seo_pagination_number' in old:raise RuntimeError('Canonical source differs; no changes')
new=old.replace(anchor,replace,1)
backupdir=root.parent/'storefront-backups'/('stage38-pagination-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backupdir.mkdir(mode=0o700,parents=True,exist_ok=True)
backup=backupdir/'index.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.seo-page38-',delete=False) as f:
  f.write(new);tmp=f.name
 os.chmod(tmp,st.st_mode&0o777);os.chown(tmp,st.st_uid,st.st_gid)
 os.replace(tmp,str(file));written=True
 cases=[
   ('/category/kompyuternye-kresla/','https://profikompany.ru/category/kompyuternye-kresla/'),
   ('/category/kompyuternye-kresla/?page=2','https://profikompany.ru/category/kompyuternye-kresla/?page=2'),
   ('/category/kompyuternye-kresla/?page=3','https://profikompany.ru/category/kompyuternye-kresla/?page=3'),
   ('/category/stoly_1/stoly-pismennye/?page=2','https://profikompany.ru/category/stoly_1/stoly-pismennye/?page=2'),
   ('/category/kompyuternye-kresla/?sort=price','https://profikompany.ru/category/kompyuternye-kresla/'),
   ('/category/kompyuternye-kresla/?page=2&sort=price','https://profikompany.ru/category/kompyuternye-kresla/'),
   ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','https://profikompany.ru/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'),
   ('/order/',None)
 ]
 checks=[]
 for route,expected in cases:
  p=subprocess.Popen(['curl','-ksSL','--max-time','33','-w','\n%{http_code}','https://profikompany.ru'+route],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  output,err=p.communicate(timeout=41)
  body,status=output.rsplit('\n',1) if '\n' in output else ('','')
  head=body.split('</head>',1)[0]
  canon=re.findall(r'<link rel="canonical" href="([^"]+)"',head)
  valid=(p.returncode==0 and status=='200'
    and (len(canon)==1 and canon[0]==expected if expected else len(canon)==0))
  checks.append({'url':route,'http':status,'canonical':canon[:2],'ok':valid})
 if not all(x['ok'] for x in checks):raise RuntimeError('Canonical smoke failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE38_APPLY='+json.dumps({'ok':True,'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as ex:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE38_ROLLBACK='+json.dumps({'error':str(ex),'restored':written},ensure_ascii=False))
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
 with tempfile.TemporaryDirectory(prefix='stage38-pagination-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage38-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE38_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
