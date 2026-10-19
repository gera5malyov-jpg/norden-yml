#!/usr/bin/env python3
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import pathlib,json
r=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
f=r/'wa-data/public/site/themes/pureMegapolis42/layouts/layout.contact.html'
t=f.read_text('utf8')
a=t.splitlines()
print('MAP90_SOURCE='+json.dumps({'file':str(f.relative_to(r)),'line_count':len(a),
 'lines':['{}:{}'.format(i+1,line[:600]) for i,line in enumerate(a) if (i<39 or i>75)]},ensure_ascii=False),flush=True)
f=r/'wa-data/public/site/themes/pureMegapolis42/index.html'
t=f.read_text('utf8')
for needle in ['megapolis-responsive-contact-map','layout.contact.html','$_contact__type','company__map']:
 p=t.find(needle)
 print('MAP90_THEME='+json.dumps({'needle':needle,'present':p>=0,'snippet':t[max(0,p-180):p+450] if p>=0 else ''},ensure_ascii=False),flush=True)
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for line in out.splitlines():
  if line.startswith('MAP90_'):print(line,flush=True)
