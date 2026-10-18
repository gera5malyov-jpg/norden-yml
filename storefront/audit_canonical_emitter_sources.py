#!/usr/bin/env python3
"""Read-only audit of canonical emitters. Never edits site or database."""
from stage52_disable_callback import execute

REMOTE=r"""set -eu
python3 - <<'PY'
import os,pathlib,json,hashlib
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
paths=[
 'wa-data/public/site/themes/pureMegapolis42/index.html',
 'wa-data/public/shop/themes/pureMegapolis42/head.html',
 'wa-data/public/shop/themes/pureMegapolis42/category.html',
 'wa-data/public/shop/themes/pureMegapolis42/index.html',
 'wa-apps/shop/themes/default/head.html',
 'wa-apps/shop/lib/actions/frontend/shopFrontendCategory.action.php',
 'wa-apps/shop/lib/classes/shopViewHelper.class.php',
]
for rel in paths:
 p=root/rel
 if not p.is_file():
  print('CANONICAL_FILE='+json.dumps({'path':rel,'exists':False}),flush=True);continue
 s=p.read_text(encoding='utf-8',errors='replace')
 lines=s.splitlines();hits=[]
 for i,line in enumerate(lines):
  if any(token in line.lower() for token in ('canonical','wa->head','head.html','robots','_seo_pagination')):
   if len(hits)<34:hits.append({'line':i+1,'excerpt':'\\n'.join(lines[max(0,i-4):min(len(lines),i+6)])[:1000]})
 print('CANONICAL_FILE='+json.dumps({'path':rel,'exists':True,'bytes':len(s),'sha':hashlib.sha256(s.encode('utf-8')).hexdigest(),'hits':hits},ensure_ascii=False),flush=True)
PY
"""
if __name__=="__main__":
  output=execute(REMOTE)
  rows=[line for line in output.splitlines() if line.startswith("CANONICAL_FILE=")]
  if len(rows)<5:raise RuntimeError("Read-only canonical source audit returned too few files")
  for row in rows:print(row,flush=True)
