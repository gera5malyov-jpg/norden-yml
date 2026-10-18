#!/usr/bin/env python3
"""Read-only audit of active canonical source and relevant flags."""
import json
from stage52_disable_callback import execute

REMOTE = r'''set -eu
python3 - <<'PY'
import pathlib,json,hashlib
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
path=root/'wa-data/public/site/themes/pureMegapolis42/index.html'
s=path.read_text(encoding='utf-8')
lines=s.splitlines()
hits=[]
for i,line in enumerate(lines):
 if ('CANONICAL' in line or 'canonical' in line or '_seo_pagination' in line or 'Scoped search indexing' in line) and i<260:
  hits.append({'line':i+1,'context':'\n'.join(lines[max(0,i-3):min(len(lines),i+7)])[:900]})
print('CANONICAL_AUDIT='+json.dumps({'sha256':hashlib.sha256(s.encode()).hexdigest(),'bytes':len(s),'count_anchor':s.count('{* CANONICAL *}'),'count_simple':s.count('<link rel="canonical" href="{$canonical|escape}" />'),'count_pagination':s.count('$_seo_pagination_number'),'hits':hits[:15]},ensure_ascii=False),flush=True)
PY
'''
if __name__ == '__main__':
    result=execute(REMOTE)
    rows=[line for line in result.splitlines() if line.startswith('CANONICAL_AUDIT=')]
    if len(rows)!=1:
        raise RuntimeError('Missing audit output')
    print(rows[0],flush=True)
