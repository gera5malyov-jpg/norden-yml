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

print('ZERO_TEMPLATES='+json.dumps([{'file':str(p.relative_to(root)),'size':p.stat().st_size,'matches':[w for w in ['product-prices','product-price','itemprop="price"','product-add','{$wa->title()','property="og:title"'] if w in p.read_text('utf8',errors='replace')]} for theme in ('site','shop') for p in (root/('wa-data/public/'+theme+'/themes/pureMegapolis42')).rglob('*.html') if p.is_file() and p.stat().st_size<450000 and any(w in p.read_text('utf8',errors='replace') for w in ('product-prices','product-price','itemprop="price"','product-add','{$wa->title()','property="og:title"'))][:65],ensure_ascii=False),flush=True)
for rel,lo,hi in [
 ('wa-data/public/site/themes/pureMegapolis42/index.html',93,130),
 ('wa-data/public/shop/themes/pureMegapolis42/product.cart.html',65,155),
 ('wa-data/public/shop/themes/pureMegapolis42/product.cart.html',179,325),
 ('wa-data/public/shop/themes/pureMegapolis42/product.html',205,260)]:
 p=root/rel
 ls=p.read_text('utf8',errors='replace').splitlines()
 print('ZERO_CONTEXT='+json.dumps({'file':rel,'start':lo,'lines':[str(i+1)+': '+ls[i][:350] for i in range(lo-1,min(hi,len(ls)))]},ensure_ascii=False),flush=True)
PY
'''
if __name__ == '__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith(('ZERO_AUDIT=','ZERO_TEMPLATES=','ZERO_CONTEXT=')): print(line, flush=True)
