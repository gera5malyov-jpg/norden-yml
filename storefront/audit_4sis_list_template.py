#!/usr/bin/env python3
"""Read-only audit: identify active category/list external image template."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from stage52_disable_callback import execute

REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json
ROOT=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
THEME=ROOT/'wa-data/public/shop/themes/pureMegapolis42'
SITE=ROOT/'wa-data/public/site/themes/pureMegapolis42'
needles=('product-item--extimg','data-src=','extimg','ext_img','itemprop="image"','isLazyLoad')
out=[]
for base in (THEME,SITE):
 for path in base.rglob('*.html'):
  if path.stat().st_size>300000:continue
  body=path.read_text(encoding='utf-8',errors='replace')
  matches=[]
  lines=body.splitlines()
  for i,line in enumerate(lines):
   if any(n in line for n in needles) and (('img' in line) or ('extimg' in line) or ('ext_img' in line)):
    matches.append({'line':i+1,'text':line[:450]})
  if matches:
   out.append({'path':str(path.relative_to(ROOT)),
               'matches':matches[:26],'size':len(body)})
print('FOURSIS_LIST_AUDIT='+json.dumps(out,ensure_ascii=False),flush=True)
PY
"""
def main():
    output=execute(REMOTE)
    if 'FOURSIS_LIST_AUDIT=' not in output:
        raise RuntimeError('No template findings')
    print(output.split('FOURSIS_LIST_AUDIT=',1)[1].split('\n',1)[0],flush=True)
if __name__ == '__main__':
    main()
