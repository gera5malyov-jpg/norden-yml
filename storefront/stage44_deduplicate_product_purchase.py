#!/usr/bin/env python3
"""Guarded removal of repeated delivery/payment sections from product page.

Preserves the trust panel and ordering controls; uncategorized products unchanged.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
source=file.read_text(encoding='utf-8')
duplicate='''          {* Purchase help: only products assigned to an existing category *}
          {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($product.category_id)}
            <p class="product-page__buy-help" style="margin:12px 0; font-size:0.9em">
              <a href="/dostavka/">Условия доставки</a>
              <span aria-hidden="true"> · </span>
              <a href="/oplata/">Способы оплаты</a>
            </p>
          {/if}
'''
features='''          {if !empty($theme_settings.product__features_custom)}
            {$theme_settings.product__features_custom}
          {/if}'''
features_new='''          {* The existing purchase trust box already covers shipping, payments and warranty.
             Suppress only this generic repeat for categorized products on profikompany.ru. *}
          {if !empty($theme_settings.product__features_custom) && ($wa->domainUrl() != 'https://profikompany.ru' || empty($product.category_id))}
            {$theme_settings.product__features_custom}
          {/if}'''
if source.count(duplicate)!=1 or source.count(features)!=1:
 raise RuntimeError('Product layout changed; duplicate segments not unique - no changes')
if source.count('class="megapolis-purchase-trust"')!=1 or source.count('class="megapolis-purchase-consultation"')!=1:
 raise RuntimeError('Existing purchase box changed; no changes')
updated=source.replace(duplicate,'',1).replace(features,features_new,1)
if 'product-page__buy-help' in updated or updated.count('megapolis-purchase-trust')<2:
 raise RuntimeError('Output validation failed before write')
base=root.parent/'storefront-backups'
base.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=base/('stage44-remove-duplicate-purchase-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backup=folder/'product.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.stage44-product-',delete=False) as tmp:
  tmp.write(updated);tmp_path=tmp.name
 os.chmod(tmp_path,st.st_mode)
 os.chown(tmp_path,st.st_uid,st.st_gid)
 os.replace(tmp_path,str(file));written=True
 checks=[]
 for route,kind in (
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','categorized'),
  ('/stul-sevyn-bukle-kofeynyy-2-sht/','uncategorized'),
  ('/category/kompyuternye-kresla/','category'),
  ('/order/','cart')
 ):
  url='https://profikompany.ru'+route+'?stage44_check='+str(os.getpid())
  p=subprocess.Popen(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  stdout,err=p.communicate(timeout=43)
  html,status=stdout.rsplit('\n',1) if '\n' in stdout else ('','')
  if kind=='categorized':
   ok=(p.returncode==0 and status=='200' and html.count('class="megapolis-purchase-trust"')==1
      and 'class="product-page__buy-help"' not in html
      and 'features features--inline features--circle' not in html
      and 'class="megapolis-purchase-consultation"' in html
      and 'class="product-prices"' in html
      and 'class="product-add"' in html
      and '<h1 class="product-page__header"' in html)
  elif kind=='uncategorized':
   ok=(p.returncode==0 and status=='200'
       and 'class="megapolis-purchase-trust"' not in html
       and 'class="product-page__buy-help"' not in html
       and 'features features--inline features--circle' in html
       and 'class="product-add"' in html)
  else:ok=p.returncode==0 and status=='200'
  checks.append({'route':route,'kind':kind,'http':status,'ok':ok})
 if not all(c['ok'] for c in checks):raise RuntimeError('Live page smoke failed '+json.dumps(checks))
 print('STAGE44_APPLY='+json.dumps({'success':True,'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE44_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
 with tempfile.TemporaryDirectory(prefix='stage44-remove-duplicate-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage44-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE44_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
