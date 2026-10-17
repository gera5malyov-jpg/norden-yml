#!/usr/bin/env python3
"""Fix truthful Product.brand microdata from an existing Bryend feature.

No modification to the Webasyst database or product images. Guarded atomic edit
of the active theme product.html with a server-side backup and smoke+rollback.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute

REMOTE=r"""set -eu
python3 - <<'PY'
import json,os,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
target=root/'wa-data/public/shop/themes/pureMegapolis42/product.html'
anchor='''<article class="product-page" itemscope itemtype="http://schema.org/Product">
{* Search snippet properties - visible product data only *}'''
replacement='''<article class="product-page" itemscope itemtype="http://schema.org/Product">
{* Search snippet properties - use verified brand feature, never infer a supplier as brand *}
{if !empty($product.features.bryend)}
  <meta itemprop="brand" content="{$product.features.bryend|escape}">
{/if}'''
urls=[
'https://profikompany.ru/riley-soft-cb2330-barnyy-stul-sidene-ekokozha-ekos-chernyy-karkas-metall-matovyy-chernyy/',
'https://profikompany.ru/academy-cb1663-stul-sidene-polipropilen-matovyy-bledno-rozovyy-karkas-metall-matovyy-chernyy/',
'https://profikompany.ru/barselona-kofeynyy-stolik-iz-iskusstvennogo-rotanga-tsvet-korichnevyy/'
]
if not target.is_file():raise RuntimeError('Missing live Product theme')
old=target.read_text(encoding='utf-8')
if old.count(anchor)!=1:raise RuntimeError('Unexpected schema header, no write')
if 'itemprop="brand"' in old:raise RuntimeError('Brand microdata already customized')
new=old.replace(anchor,replacement,1)
base=root.parent/'storefront-backups'
base.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=base/('yandex-verified-brand-'+datetime.utcnow().strftime('%Y%m%dT%H%M%S')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backup=folder/'product.html.bak'
shutil.copy2(str(target),str(backup))
os.chmod(str(backup),0o600)
def atomic(s):
 st=target.stat()
 fd,tmp=tempfile.mkstemp(prefix='.yandex-schema-',dir=str(target.parent))
 try:
  with os.fdopen(fd,'w') as f:f.write(s)
  os.chmod(tmp,st.st_mode&0o777);os.chown(tmp,st.st_uid,st.st_gid)
  os.rename(tmp,str(target))
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def fetch(url,i):
 p=subprocess.run(['curl','-ksSL','--max-time','32','-w','\n%{http_code}',
     url+'?brand_schema_check='+str(os.getpid())+'_'+str(i)],stdout=subprocess.PIPE,
     stderr=subprocess.PIPE,universal_newlines=True,timeout=40)
 body,status=p.stdout.rsplit('\n',1) if '\n' in p.stdout else ('','unknown')
 brand_count=body.count('<meta itemprop="brand" content="Connubia">')
 return {'http':status,'curl':p.returncode,'brand_count':brand_count,
         'product':'itemscope itemtype="http://schema.org/Product"' in body,
         'offer':'itemprop="offers" itemscope itemtype="http://schema.org/Offer"' in body,
         'image': '<img itemprop="image" src="https://' in body,
         'price':'itemprop="price" content="' in body,
         'availability':'itemprop="availability" href="http://schema.org/' in body}
written=False
try:
 if target.read_text(encoding='utf-8')!=old:
  raise RuntimeError('Concurrent product theme modification')
 atomic(new);written=True
 attempts=[]
 for i in range(8):
  checks=[fetch(u,i) for u in urls]
  ok=(all(c['http']=='200' and c['curl']==0 and c['product'] and c['offer']
          and c['image'] and c['price'] and c['availability'] for c in checks)
       and all(c['brand_count']==1 for c in checks[:2]))
  attempts.append({'iteration':i,'checks':checks,'ok':ok})
  if ok:break
  time.sleep(2)
 if not attempts[-1]['ok']:
  raise RuntimeError('Product schema smoke failed: '+json.dumps(attempts[-1])[:1500])
 print('STAGE52_YANDEX_BRAND_FIX='+json.dumps({
     'result':'success','backup':str(backup),'checks':attempts[-1]},ensure_ascii=False),flush=True)
except Exception as ex:
 if written:shutil.copy2(str(backup),str(target))
 print('STAGE52_YANDEX_BRAND_FIX='+json.dumps({
    'result':'rolled_back','backup':str(backup),'error':str(ex)[:1300]},
    ensure_ascii=False),flush=True)
 raise
PY
"""
def main():
 out=execute(REMOTE)
 if '"result": "success"' not in out and '"result":"success"' not in out:
  raise RuntimeError('No verified brand markup deployment')
if __name__=='__main__':main()
