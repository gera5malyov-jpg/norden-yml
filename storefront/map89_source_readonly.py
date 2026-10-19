#!/usr/bin/env python3
"""Read-only find embedded Yandex map in active Webasyst templates, public pages and theme sources."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
patterns=['f071cbf134e324effb2e33e2d565291b4ecc591045605639489b34f0c55b2769','api-maps.yandex.ru/services/constructor','contact__map','contact__items','Юридический адрес:']
dirs=[root/'wa-data/public/site/themes/pureMegapolis42',root/'wa-data/public/shop/themes/pureMegapolis42',root/'wa-apps/shop/themes',root/'wa-apps/site/themes']
found=[]
for d in dirs:
 if not d.is_dir():continue
 for f in d.rglob('*'):
  if not f.is_file() or f.suffix not in ('.html','.php','.js','.css','.json','.xml'):continue
  if f.stat().st_size>900000:continue
  try:s=f.read_text('utf8',errors='replace')
  except Exception:continue
  for needle in patterns:
   if needle.lower() in s.lower():
    snippets=[]
    for m in list(re.finditer(re.escape(needle),s,re.I))[:3]:
     lineno=s.count('\n',0,m.start())+1
     snippets.append({'line':lineno,'snippet':s[max(0,m.start()-480):m.start()+570].replace('\n',' ')[:1100]})
    found.append({'file':str(f.relative_to(root)),'needle':needle,'n':s.lower().count(needle.lower()),'sample':snippets})
print('MAP89_SCAN='+json.dumps({'records':found[:90],'record_count':len(found)},ensure_ascii=False),flush=True)
PY
'''
if __name__=='__main__':
 result=execute(REMOTE)
 for line in result.splitlines():
  if line.startswith('MAP89_'):print(line,flush=True)
 if 'MAP89_SCAN=' not in result:raise RuntimeError('No inspection result')
