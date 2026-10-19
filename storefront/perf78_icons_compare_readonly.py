#!/usr/bin/env python3
"""Read-only storefront Font Awesome and comparison route inventory."""
from stage52_disable_callback import execute
REMOTE = r'''set -eu
python3 - <<'PY'
import os,json,re,pathlib,collections
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
folders=[root/'wa-data/public/site/themes/pureMegapolis42',root/'wa-data/public/shop/themes/pureMegapolis42']
for folder in folders:
 matches=[]
 for p in folder.rglob('*'):
  if not p.is_file() or p.suffix not in ('.html','.js','.php','.css') or '/fonts/' in str(p):continue
  if p.stat().st_size>900000:continue
  if p.name.startswith(('fa-solid','fa-brands','fa-regular','fontawesome','bundle.min','theme.min','shop.theme.min')):continue
  try: s=p.read_text('utf8',errors='replace')
  except:continue
  hits=[]
  for i,l in enumerate(s.splitlines()):
   if re.search(r'fontawesome\.min\.js|fa-(?:solid|brands|regular)\.min\.js|\b(?:fas|far|fab)\b|class=["\'][^"\']*fa-|FontAwesome|fontawesome',l,re.I):
    hits.append({'line':i+1,'snippet':l.strip()[:210]})
    if len(hits)>=13:break
  if hits:matches.append({'file':str(p.relative_to(root)),'hits':hits})
 print('PERF78_FONT_USAGE='+json.dumps({'theme':folder.name,'files':len(matches),'matches':matches[:48]},ensure_ascii=False),flush=True)
i=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
s=i.read_text('utf8')
for marker in ["$_seo_info_url == '/search/'","noindex,follow","fontawesome.min.js","fa-brands.min.js","fa-regular.min.js","fa-solid.min.js","megapolis-gallery-cls-20261019"]:
 idx=s.find(marker)
 print('PERF78_THEME='+json.dumps({'marker':marker,'count':s.count(marker),'context':s[max(0,idx-270):idx+400] if idx>=0 else None},ensure_ascii=False),flush=True)
PY
'''
if __name__=='__main__':
 output=execute(REMOTE)
 for ln in output.splitlines():
  if ln.startswith('PERF78_'):print(ln,flush=True)
 if 'PERF78_THEME=' not in output:raise RuntimeError('No theme context returned')
