#!/usr/bin/env python3
"""Read-only discover exact admin tab/field for disappeared YML PRO stock reset."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
python3 - <<'PY'
import os,re,json,pathlib
base=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml')
dirs=['templates','js','css','lib/config']
matched=[]
for d in dirs:
 root=base/d
 if not root.is_dir():continue
 for f in root.rglob('*'):
  if not f.is_file() or f.suffix.lower() not in ('.html','.tpl','.js','.json','.php'):continue
  if f.stat().st_size>1000000:continue
  try:src=f.read_text(encoding='utf-8',errors='replace')
  except Exception:continue
  lines=src.splitlines()
  for i,line in enumerate(lines):
   if not re.search('исчезн|пропавш|отсутствующ|обнул|сброс.*остат|disappear|zero_disappear|zeroDisappeared|disappeared_action|wipe_exceeded|missing_product',line,re.I):continue
   # publish only settings keys or user-visible labels in <=210 chars, never importer internals
   excerpt=line.strip()
   excerpt=re.sub(r'https?://\\S+','[URL]',excerpt)
   if len(excerpt)>210:excerpt=excerpt[:210]
   matched.append({'path':str(f.relative_to(base)),'line':i+1,'context':excerpt})
   if len(matched)>100:break
  if len(matched)>120:break
print('YML_DISAPPEARED_UI='+json.dumps(matched[:95],ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("YML_DISAPPEARED_UI="):print(line,flush=True)
if "YML_DISAPPEARED_UI=" not in out:raise RuntimeError("Missing UI metadata")
