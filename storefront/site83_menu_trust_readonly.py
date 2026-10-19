#!/usr/bin/env python3
"""Read-only navigation and purchase template source inventory."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,re,json,collections
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
dirs=[root/'wa-data/public/site/themes/pureMegapolis42',root/'wa-data/public/shop/themes/pureMegapolis42']
def emit(k,v):print('SITE83_'+k+'='+json.dumps(v,ensure_ascii=False),flush=True)
terms=['layout.categories','layout.header','categories-nav','category-nav','mega-menu','catalog-menu','nav-categories','component.menu','menu__','product-cart','product-stocks']
for d in dirs:
 records=[]
 for p in d.rglob('*.html'):
  if p.stat().st_size>400000:continue
  text=p.read_text('utf8',errors='replace')
  counts={w:text.count(w) for w in terms if w in text}
  if counts:records.append({'file':str(p.relative_to(root)),'bytes':p.stat().st_size,'counts':counts})
 emit('FILES',{'dir':str(d.relative_to(root)),'files':sorted(records,key=lambda x:sum(x['counts'].values()),reverse=True)[:32]})
candidates=[
 'wa-data/public/site/themes/pureMegapolis42/layouts/layout.header.html',
 'wa-data/public/site/themes/pureMegapolis42/layouts/layout.categories.html',
 'wa-data/public/site/themes/pureMegapolis42/components/component.categories.html',
 'wa-data/public/site/themes/pureMegapolis42/components/component.menu.html',
 'wa-data/public/site/themes/pureMegapolis42/index.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.cart.html',
 'wa-data/public/shop/themes/pureMegapolis42/category.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.html']
for n in candidates:
 p=root/n
 if not p.is_file():continue
 text=p.read_text('utf8',errors='replace'); lines=text.splitlines(); excerpts=[]
 for i,line in enumerate(lines):
  if any(x in line for x in ['layout.categories','layout.header','nav-categories','catalog-menu','categories-nav','mega-menu','product-stocks','product-prices','product-add','main__nav','wa->shop->categories(','category_tree','$_navigation','category_menu']):
   excerpts.append({'line':i+1,'text':'\n'.join(str(j+1)+':'+lines[j][:260] for j in range(max(0,i-2),min(len(lines),i+4)))})
  if len(excerpts)>22:break
 emit('SOURCE',{'file':n,'size':len(text),'excerpts':excerpts})
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for ln in out.splitlines():
  if ln.startswith('SITE83_'):print(ln,flush=True)
 if 'SITE83_FILES=' not in out:raise RuntimeError('No template inventory')
