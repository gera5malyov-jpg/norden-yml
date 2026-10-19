#!/usr/bin/env python3
"""Read-only detail/list feature markup context from live storefront theme."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-data/public/shop/themes/pureMegapolis42')
for name in ('product.html','product.info.html','products.features.html','products.html','list-thumbs.html'):
 p=root/name
 if not p.exists():continue
 lines=p.read_text('utf8',errors='replace').splitlines()
 markers=['$features[$f_code].name','$features[$f_code].type','$f_value','$_features','features-list','product-item__description','itemscope','itemprop="description"','product__features']
 ix=set()
 for i,line in enumerate(lines):
  if any(m in line for m in markers):
   ix.update(range(max(0,i-5),min(len(lines),i+11)))
 output=['%d: %s'%(i+1,lines[i][:400]) for i in sorted(ix)]
 print('FEATURE_THEME='+json.dumps({'file':name,'lines':output[:360]},ensure_ascii=False),flush=True)
PY
'''
if __name__=='__main__':
 r=execute(REMOTE)
 for ln in r.splitlines():
  if ln.startswith('FEATURE_THEME='):print(ln,flush=True)
