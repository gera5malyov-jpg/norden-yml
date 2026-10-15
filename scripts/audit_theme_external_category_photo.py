#!/usr/bin/env python3
"""Read-only inspect category/listing image fallbacks in the installed storefront theme."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/shop/themes/pureMegapolis42'
out=[]
for f in theme.rglob('*.html'):
 try:s=f.read_text(encoding='utf-8',errors='replace')
 except:continue
 lines=s.splitlines()
 match=[]
 for i,line in enumerate(lines):
  if re.search(r'\$product\.(image|summary)|getImageUrl\(|product\.image_url|product\.images|defaultImage|product\.image_id',line,re.I):
   if (re.search(r'item|card|thumb|tile|product|category|catalog|list|search',f.name,re.I)
       and len(match)<15):
    match.append({'line':i+1,'text':line.strip()[:210]})
 if match:
  out.append({'file':str(f.relative_to(theme)),'matches':match[:12]})
print('LISTPHOTO_THEME='+json.dumps(out[:65],ensure_ascii=False))
PY
"""
r=execute(REMOTE)
for line in r.splitlines():
 if line.startswith("LISTPHOTO_THEME="):print(line,flush=True)
