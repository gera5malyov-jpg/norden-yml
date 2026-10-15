#!/usr/bin/env python3
"""Read-only inspect image branch order in public storefront product tile templates."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
python3 - <<'PY'
import pathlib
t=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-data/public/shop/themes/pureMegapolis42')
for name,lo,hi in [('products.html',40,130),('list-thumbs.html',30,120)]:
 p=t/name
 if not p.is_file():continue
 for i,line in enumerate(p.read_text(encoding='utf-8',errors='replace').splitlines()):
  if lo<=i+1<=hi:
   print('PHOTO_TILE_CODE='+name+':'+str(i+1)+':'+line.strip()[:280])
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("PHOTO_TILE_CODE="):print(line,flush=True)
