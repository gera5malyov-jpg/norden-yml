#!/usr/bin/env python3
"""Read-only inspection of product orderability markup in active Webasyst Smarty theme."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,json,re
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
themes=[root/'wa-data/public/shop/themes/pureMegapolis42',root/'wa-data/public/site/themes/pureMegapolis42']
for theme in themes:
 for path in sorted(theme.rglob('*.html')):
  if not path.is_file() or path.stat().st_size>750000:continue
  text=path.read_text('utf8',errors='replace')
  markers=['product-stocks','product-cart','product-quantity','product-prices','s-product-form','product-add__button','product__stock','data-sku-count']
  matched=[m for m in markers if m in text]
  if not matched:continue
  if 'product-stocks' not in text and 's-product-form' not in text and 'product-prices' not in text:continue
  lines=text.splitlines()
  result=[]
  for i,line in enumerate(lines):
   if any(m in line for m in markers):
    low=max(0,i-6);hi=min(len(lines),i+8)
    result.append({'from':low+1,'to':hi,'text':'\n'.join('{}:{}'.format(j+1,lines[j][:300]) for j in range(low,hi))[:2600]})
   if len(result)>=12:break
  print('ORDER81_TEMPLATE='+json.dumps({'file':str(path.relative_to(root)),'size':len(text),'matched':matched,'blocks':result},ensure_ascii=False),flush=True)
PY
'''
if __name__=='__main__':
 output=execute(REMOTE)
 for line in output.splitlines():
  if line.startswith('ORDER81_'): print(line,flush=True)
 if 'ORDER81_TEMPLATE=' not in output:raise RuntimeError('No product template information found')
