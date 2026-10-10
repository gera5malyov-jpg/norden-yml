#!/usr/bin/env python3
"""Stage 18: replace demo homepage company copy and broken image.

Only a domain-scoped welcome template is changed; products, inventory and other domains remain untouched.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,tempfile,shutil,subprocess,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.welcome.html'
orig=file.read_text(encoding='utf-8')
needle='''        {$_welcome_text}'''
if orig.count(needle)!=1 or 'welcome-note__megapolis-copy' in orig:
 raise RuntimeError('Homepage theme source changed, no writes')
content='''        {if $wa->domainUrl() == 'https://profikompany.ru'}
          <section class="welcome-note__megapolis-copy" aria-label="О компании Мегаполис">
            <h1 class="welcome__title title size-typo-xxxl">Мегаполис Маркет - мебель для дома и офиса</h1>
            <p>«Мегаполис Маркет» - интернет-магазин мебели и предметов интерьера. В нашем каталоге представлены кресла, стулья, столы, шкафы и другая мебель для дома, офиса и общественных пространств.</p>
            <p>На сайте можно изучить характеристики и фотографии товаров, сравнить модели и оформить заказ. Актуальные цены и доступность представлены в карточках товаров. Информация о способах оплаты и условиях доставки находится в разделах <a href="/oplata/">«Оплата»</a> и <a href="/dostavka/">«Доставка»</a>.</p>
            <p>Нужна помощь с выбором? Свяжитесь с нами по телефону <a href="tel:+78122449264">+7 (812) 244-92-64</a> или напишите на <a href="mailto:shop@office-mag.com">shop@office-mag.com</a>. Поможем уточнить характеристики и условия заказа.</p>
          </section>
        {else}
          {$_welcome_text}
        {/if}'''
updated=orig.replace(needle,content,1)
base=root.parent/'storefront-backups'
base.mkdir(mode=0o700,parents=True,exist_ok=True)
bd=base/('stage18-welcome-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
bd.mkdir(mode=0o700)
backup=bd/'layout.welcome.html'
shutil.copy2(str(file),str(backup))
os.chmod(str(backup),0o600)
written=False
try:
 st=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.welcome-copy-',delete=False) as fp:
  fp.write(updated);tmp=fp.name
 os.chmod(tmp,st.st_mode);os.chown(tmp,st.st_uid,st.st_gid)
 os.replace(tmp,str(file));written=True
 checks=[]
 for path in ('/','/category/kompyuternye-kresla/','/order/'):
  url='https://profikompany.ru'+path+'?stage18verify='+str(int(time.time()))
  p=subprocess.Popen(['curl','-k','-sS','-L','--max-time','32','-w','\n%{http_code}',url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=40)
  html,status=out.rsplit('\n',1) if '\n' in out else ('','')
  ok=(p.returncode==0 and status=='200')
  d={'path':path,'http':status}
  if path=='/':
   d['new_text']='Мегаполис Маркет - мебель для дома и офиса' in html
   d['old_text_absent']='Наш магазин дает вам уникальный шанс' not in html
   d['broken_image_absent']='pure.easyweb.su/wa-data/public/shop/img/about_2.jpg' not in html
   d['contact_links']=('mailto:shop@office-mag.com' in html and '/dostavka/' in html)
   ok=ok and all((d['new_text'],d['old_text_absent'],d['broken_image_absent'],d['contact_links']))
  d['ok']=ok
  checks.append(d)
 if not all(x['ok'] for x in checks):raise RuntimeError('Live homepage checks failed: '+json.dumps(checks,ensure_ascii=False))
 print('STAGE18_APPLY='+json.dumps({'success':True,'backup':str(backup),'checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE18_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
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
  with tempfile.TemporaryDirectory(prefix='storefront-stage18-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage18-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STAGE18_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
