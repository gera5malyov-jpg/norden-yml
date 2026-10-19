#!/usr/bin/env python3
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
files=[
 'wa-data/public/site/themes/pureMegapolis42/components/component.catalog.inline.html',
 'wa-data/public/site/themes/pureMegapolis42/components/component.catalog.html',
 'wa-data/public/site/themes/pureMegapolis42/layouts/layout.categories.html',
 'wa-data/public/site/themes/pureMegapolis42/layouts/layout.header.html',
 'wa-data/public/site/themes/pureMegapolis42/index.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.cart.html'
]
for f in files:
 p=root/f
 if not p.is_file():continue
 a=p.read_text('utf8',errors='replace').splitlines()
 if f.endswith('index.html'):
  selected=[(i+1,x) for i,x in enumerate(a) if ('component.catalog' in x or 'layout.categories' in x or 'layout.header' in x or 'header__' in x or 'catalog' in x and 'include' in x)][:70]
 elif f.endswith('product.cart.html'):
  selected=[(i+1,a[i]) for i in range(min(332,len(a))) if 180<=i<=331]
 else:
  selected=[(i+1,x) for i,x in enumerate(a[:350])]
 print('SITE84_FILE='+json.dumps({'file':f,'line_count':len(a),'lines':[str(i)+' '+s[:430] for i,s in selected]},ensure_ascii=False),flush=True)
PY
'''
if __name__=='__main__':
 x=execute(REMOTE)
 for l in x.splitlines():
  if l.startswith('SITE84_FILE='):print(l,flush=True)
