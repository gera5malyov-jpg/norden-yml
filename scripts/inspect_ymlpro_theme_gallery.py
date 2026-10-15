#!/usr/bin/env python3
"""Read-only inspect product gallery rendering condition in active theme."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'storefront'))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
P=/home/web/vm-23f9aff9.na4u.ru/www/wa-data/public/shop/themes/pureMegapolis42/product.html
test -f "$P"
python3 - "$P" <<'PY'
import sys,re,json
lines=open(sys.argv[1]).read().splitlines()
selected=[]
for i,line in enumerate(lines):
 if 'extimg' in line.lower() or 'gallery' in line.lower() or ('images' in line.lower() and ('if ' in line or 'foreach' in line)):
  selected.append(i)
keep=set()
for i in selected:
 for j in range(max(0,i-3),min(len(lines),i+4)):keep.add(j)
for j in sorted(keep):
 line=lines[j].strip()
 if len(line)>250:line=line[:250]
 print('THEME_GALLERY_LINE='+str(j+1)+':'+line)
PY
"""
r=execute(REMOTE)
for line in r.splitlines():
 if line.startswith("THEME_GALLERY_LINE="):print(line,flush=True)
