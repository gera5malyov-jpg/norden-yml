#!/usr/bin/env python3
"""Read-only zero-price storefront template audit. No secrets or credentials printed."""
from stage52_disable_callback import execute
REMOTE = r'''set -eu
python3 - <<'PY'
import pathlib, re, json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
paths=[
 'wa-data/public/site/themes/pureMegapolis42/index.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.cart.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.item.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.list.html',
 'wa-data/public/shop/themes/pureMegapolis42/product.info.html',
 'wa-data/public/shop/themes/pureMegapolis42/category.html',
 'wa-data/public/shop/themes/pureMegapolis42/home.html',
 'wa-data/public/shop/themes/pureMegapolis42/search.html',
]
for rel in paths:
 p=root/rel
 if not p.exists():
  print('ZERO_AUDIT='+json.dumps({'file':rel,'missing':True}),flush=True);continue
 lines=p.read_text('utf8',errors='replace').splitlines()
 matches=[]
 pattern=re.compile(r'product.price|sku.price|price_html|price\s*|meta\(|itemprop="offers"|itemprop="price"|product-add|<title|og:title|description|include file="product|schema.org/Offer',re.I)
 for i,line in enumerate(lines):
  if not pattern.search(line):continue
  if line.strip().startswith(('*','//')):continue
  if len(matches)>=55:break
  matches.append({'line':i+1,'text':line.strip()[:280]})
 print('ZERO_AUDIT='+json.dumps({'file':rel,'size':p.stat().st_size,'lines':len(lines),'matches':matches},ensure_ascii=False),flush=True)
PY
'''
if __name__ == '__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('ZERO_AUDIT='): print(line, flush=True)
