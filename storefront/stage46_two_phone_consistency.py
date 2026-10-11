#!/usr/bin/env python3
"""Guarded paired public phone contacts in header, company intro and product trust section.

Shows SPB and Moscow numbers; does not change shop data or product information.
Copies active Smarty template to private backup, rolls back if smoke fails.
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
site=root/'wa-data/public/site/themes/pureMegapolis42'
shop=root/'wa-data/public/shop/themes/pureMegapolis42'
phonefile=site/'components/component.phones.html'
welcome=site/'layouts/layout.welcome.html'
product=shop/'product.html'
files=[phonefile,welcome,product]
oldtext={f:f.read_text(encoding='utf-8') for f in files}
phone_old='''     {if $_phones_type|strpos:"second" !== false && !empty($_company__phone_second)}
       <a class="phones__link phones__link--phone" href="tel:{$_company__phone_second|replace:" ":""}">
         {$_company__phone_second}
       </a>
     {elseif !empty($_company__phone_main)}
       <a class="phones__link phones__link--phone" href="tel:{$_company__phone_main|replace:" ":""}">
         {$_company__phone_main}
       </a>
     {/if}'''
phone_new='''     {* megapolis-paired-phones-2026: preserve theme phone selectors and show both contact numbers *}
     {if $_phones_type|strpos:"second" !== false && !empty($_company__phone_second)}
       {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($_company__phone_main)}
         <a class="phones__link phones__link--phone" href="tel:{$_company__phone_main|replace:" ":""}">{$_company__phone_main}</a>
       {/if}
       <a class="phones__link phones__link--phone" href="tel:{$_company__phone_second|replace:" ":""}">
         {$_company__phone_second}
       </a>
     {elseif !empty($_company__phone_main)}
       <a class="phones__link phones__link--phone" href="tel:{$_company__phone_main|replace:" ":""}">
         {$_company__phone_main}
       </a>
       {if $wa->domainUrl() == 'https://profikompany.ru' && !empty($_company__phone_second) && $_phones_type|strpos:"phone" === false}
         <a class="phones__link phones__link--phone" href="tel:{$_company__phone_second|replace:" ":""}">{$_company__phone_second}</a>
       {/if}
     {/if}'''
welcome_old='''<p>Нужна помощь с выбором? Свяжитесь с нами по телефону <a href="tel:+78122449264">+7 (812) 244-92-64</a> или напишите на <a href="mailto:shop@office-mag.com">shop@office-mag.com</a>. Поможем уточнить характеристики и условия заказа.</p>'''
welcome_new='''<p>Нужна помощь с выбором? Позвоните по телефонам <a href="tel:+78122449264">+7 (812) 244-92-64</a> (Санкт-Петербург) или <a href="tel:+74996776332">+7 (499) 677-63-32</a> (Москва), либо напишите на <a href="mailto:shop@office-mag.com">shop@office-mag.com</a>. Поможем уточнить характеристики и условия заказа.</p>'''
product_old='''<p class="megapolis-purchase-trust__contact">Поможем уточнить детали: <a href="tel:+78122449264">+7 (812) 244-92-64</a></p>'''
product_new='''<p class="megapolis-purchase-trust__contact">Поможем уточнить детали: <a href="tel:+78122449264">+7 (812) 244-92-64</a> (СПб), <a href="tel:+74996776332">+7 (499) 677-63-32</a> (Москва)</p>'''
updates=[(phonefile,phone_old,phone_new),(welcome,welcome_old,welcome_new),(product,product_old,product_new)]
for file,needle,replacement in updates:
 if oldtext[file].count(needle)!=1 or 'megapolis-paired-phones-2026' in oldtext[file]:
  raise RuntimeError('Preflight: unexpected content for '+str(file))
 if oldtext[file].count(replacement):
  raise RuntimeError('Preflight: patch already present '+str(file))
newtext={file:oldtext[file].replace(needle,replacement,1) for file,needle,replacement in updates}
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backups/('stage46-two-phones-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
files_saved={}
for i,file in enumerate(files):
 saved=folder/('%02d-%s'%(i,file.name))
 shutil.copy2(str(file),str(saved))
 os.chmod(str(saved),0o600)
 files_saved[file]=saved
written=[]
try:
 for file in files:
  stat=file.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.stage46-',delete=False) as tmp:
   tmp.write(newtext[file]);tmp_path=tmp.name
  os.chmod(tmp_path,stat.st_mode)
  os.chown(tmp_path,stat.st_uid,stat.st_gid)
  os.replace(tmp_path,str(file))
  written.append(file)
 checks=[]
 for route,kind in (
  ('/','home'),('/category/kompyuternye-kresla/','category'),
  ('/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','categorized'),
  ('/stul-sevyn-bukle-kofeynyy-2-sht/','uncategorized'),
  ('/proizvoditeli/','page'),('/dostavka/','info'),('/garantiya/','info'),('/order/','checkout')
 ):
  url='https://profikompany.ru'+route+'?stage46_verify='+str(os.getpid())
  p=subprocess.Popen(['curl','-ksSL','--max-time','40','-w','\n%{http_code}',url],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  data,err=p.communicate(timeout=48)
  html,status=data.rsplit('\n',1) if '\n' in data else ('','')
  # All pages must show both telephone links through the reusable header.
  header=html.split('class="header-top',1)[-1].split('</header>',1)[0]
  header_both='tel:+7(812)2449264' in header and 'tel:+7(499)6776332' in header
  if kind=='home':
   ok=p.returncode==0 and status=='200' and header_both and
     '<a href="tel:+74996776332">+7 (499) 677-63-32</a>' in html and
     'mailto:shop@office-mag.com' in html
  elif kind=='categorized':
   box=html.split('class="megapolis-purchase-trust"',1)[-1].split('</section>',1)[0]
   both=('tel:+78122449264' in box and 'tel:+74996776332' in box)
   ok=(p.returncode==0 and status=='200' and header_both and both and
     'class="product-add"' in html and 'class="product-prices"' in html)
  elif kind=='uncategorized':
   ok=p.returncode==0 and status=='200' and header_both and 'class="megapolis-purchase-trust"' not in html
  else:
   ok=p.returncode==0 and status=='200' and header_both
  checks.append({'route':route,'http':status,'header_both':header_both,'ok':bool(ok)})
 if not all(x['ok'] for x in checks):
  raise RuntimeError('Live two-phone verification failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE46_APPLY='+json.dumps({'ok':True,'backups':str(folder),
   'files':[str(f.relative_to(root)) for f in written],'checks':checks},ensure_ascii=False))
except Exception as e:
 for file in reversed(written):
  shutil.copy2(str(files_saved[file]),str(file))
 print('STAGE46_ROLLBACK='+json.dumps({'reason':str(e),'restored':[str(f.relative_to(root)) for f in written]},ensure_ascii=False))
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
 with tempfile.TemporaryDirectory(prefix='stage46-phones-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage46-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE46_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
