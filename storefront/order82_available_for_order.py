#!/usr/bin/env python3
"""Scoped display-only orderability label for profikompany.ru."""
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import pathlib,hashlib,datetime,shutil,subprocess,tempfile,os,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
target=root/'wa-data/public/shop/themes/pureMegapolis42/product.cart.html'
start=target.read_text('utf8')
needle='{if $sku.stock}'
marker='megapolis-orderable-20261019'
replace="""{if $wa->domainUrl() == 'https://profikompany.ru'}
  {if $product.status && $sku.available && ($wa->shop->settings('ignore_stock_count') || $sku.count === null || $sku.count > 0)}
    <span class="product-stocks__text megapolis-orderable-20261019" style="color:#16803c;font-weight:600">Доступен для заказа</span>
  {/if}
{elseif $sku.stock}"""
if start.count(needle)!=1 or marker in start:raise RuntimeError('Template preflight failed')
if '$product_available' not in start or '$sku.available' not in start:raise RuntimeError('Purchase rule missing')
print('ORDER82_PREFLIGHT=source_and_buy_rules_ok',flush=True)
urls=['/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/','/stul-prozrachnyy-pedrali-queen/',
      '/','/category/kompyuternye-kresla/','/category/kompyuternye-kresla/?page=2',
      '/oplata/','/dostavka/','/compare/1186404,1186400/','/cart/','/order/']
def fetch(path):
 p=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','28','-w','\n%{http_code}',
  'https://profikompany.ru'+path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 data,error=p.communicate(timeout=35)
 html,code=data.rsplit('\n',1)
 return {'code':code,'rc':p.returncode,'label':html.count(marker),'disabled':('value="Купить" disabled="disabled"' in html),'title':html.split('</head>',1)[0].count('<title'),
 'robots':'noindex,follow' in html.split('</head>',1)[0]}
before=[fetch(u) for u in urls]
if not all(p['code']=='200' and p['title']==1 for p in before):
 raise RuntimeError('Live baseline not healthy')
print('ORDER82_PREFLIGHT=ten_routes_healthy',flush=True)
if not before[7]['robots'] or before[2]['robots'] or not before[1]['disabled'] or before[0]['disabled']:raise RuntimeError('SEO or orderability preflight failed')
print('ORDER82_PREFLIGHT=seo_guards_healthy',flush=True)
folder=root.parent/'storefront-backups'/('order82-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
folder.mkdir(parents=True,mode=0o700)
backup=folder/'product.cart.html';shutil.copy2(str(target),str(backup))
if backup.read_text('utf8')!=start:raise RuntimeError('Backup verification failed')
print('ORDER82_BACKUP='+str(backup),flush=True)
updated=start.replace(needle,replace,1)
saved=False
try:
 st=target.stat()
 with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=str(target.parent),delete=False) as fp:
  fp.write(updated);tmp=fp.name
 os.chmod(tmp,st.st_mode & 0o777);os.chown(tmp,st.st_uid,st.st_gid)
 if target.read_text('utf8')!=start:raise RuntimeError('Concurrent change')
 os.rename(tmp,str(target));saved=True
 for i,u in enumerate(urls):
  value=fetch(u);good=(value['code']=='200' and value['title']==1 and value['robots']==before[i]['robots']
    and (value['label']>0 if i==0 else value['label']==0) and value['disabled']==before[i]['disabled'])
  print('ORDER82_TEST='+json.dumps({'route':u,'ok':good,'label':value['label']}),flush=True)
  if not good:raise RuntimeError('Postcheck failed: '+u)
 print('ORDER82_DEPLOY=success',flush=True)
except Exception as err:
 if saved and target.read_text('utf8')==updated:
  shutil.copy2(str(backup),str(target))
  print('ORDER82_ROLLBACK=done',flush=True)
 raise
PY
'''
if __name__ == '__main__':
 result=execute(REMOTE)
 for line in result.splitlines():
  if line.startswith('ORDER82_'):print(line,flush=True)
 if 'ORDER82_DEPLOY=success' not in result:raise RuntimeError('No verified deployment')
