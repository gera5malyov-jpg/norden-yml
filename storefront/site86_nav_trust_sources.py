#!/usr/bin/env python3
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
for base in ['wa-data/public/site/themes/pureMegapolis42','wa-data/public/shop/themes/pureMegapolis42']:
 for p in (root/base).rglob('*.html'):
  if p.stat().st_size>450000:continue
  s=p.read_text('utf8',errors='replace')
  if 'mobile-nav__catalog' in s or 'megapolis-purchase-trust' in s:
   lines=s.splitlines()
   groups=[]
   for i,line in enumerate(lines):
    if 'mobile-nav__catalog' in line or 'megapolis-purchase-trust' in line:
     lo=max(0,i-24);hi=min(len(lines),i+67)
     groups.append('\n'.join(str(j+1)+':'+lines[j][:360] for j in range(lo,hi)))
     if len(groups)>=4:break
   print('SITE86_SRC='+json.dumps({'file':str(p.relative_to(root)),'lines':len(lines),'blocks':groups},ensure_ascii=False),flush=True)
PY
'''
if __name__=='__main__':
 x=execute(REMOTE)
 for l in x.splitlines():
  if l.startswith('SITE86_SRC='):print(l,flush=True)
