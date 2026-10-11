#!/usr/bin/env python3
"""Guarded product shipping consultation CTA, no catalog or order edits.

Adds a lead route using existing storefront callback modal for categorized products.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile,re
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
f=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
source=f.read_text(encoding='utf-8')
pat=r'(<p class="megapolis-purchase-trust__contact">[\s\S]*?</p>)'
matches=list(re.finditer(pat,source))
if len(matches)!=1 or 'tel:+78122449264' not in matches[0].group(1) or 'megapolis-purchase-consultation' in source:
 raise RuntimeError('Product trust block has changed; do not edit')
new_block=matches[0].group(1)+'''
              <p class="megapolis-purchase-consultation">
                <a href="#callback-modal">Уточнить стоимость и срок доставки</a>
              </p>'''
updated=source[:matches[0].start()]+new_block+source[matches[0].end():]
base=root.parent/'storefront-backups'
base.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=base/('stage40-delivery-callback-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
shutil.copy2(str(f),str(folder/'product.html'))
os.chmod(str(folder/'product.html'),0o600)
written=False
try:
 st=f.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(f.parent),prefix='.stage40-',delete=False) as t:
  t.write(updated);tmp=t.name
 os.chmod(tmp,st.st_mode);os.chown(tmp,st.st_uid,st.st_gid)
 os.replace(tmp,str(f));written=True
 checks=[]
 for path,expected in (
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',True),
  ('/stul-sevyn-bukle-kofeynyy-2-sht/',False),
  ('/category/kompyuternye-kresla/',False),
  ('/order/',False)
 ):
  p=subprocess.run(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',
   'https://profikompany.ru'+path+'?stage40_check='+str(os.getpid())],
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=43)
  page,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','')
  found='class="megapolis-purchase-consultation"' in page and '<a href="#callback-modal">Уточнить стоимость и срок доставки</a>' in page
  has_base_form='href="#callback-modal"' in page
  ok=p.returncode==0 and status=='200' and found==expected and has_base_form
  checks.append({'page':path,'http':status,'delivery_callback':found,'expected':expected,'ok':ok})
 if not all(x['ok'] for x in checks):raise RuntimeError('Checkout CTA smoke failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE40_APPLY='+json.dumps({'ok':True,'backup':str(folder),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(folder/'product.html'),str(f))
 print('STAGE40_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
 with tempfile.TemporaryDirectory(prefix='stage40-callback-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage40-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE40_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
