#!/usr/bin/env python3
"""Read only inspect sanitized SQL causing count overflow and YML stock option names."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
p=root/'wa-log/db.log'
with p.open('rb') as f:
 f.seek(max(0,p.stat().st_size-16000000))
 t=f.read().decode('utf-8','replace')
matches=list(re.finditer("Out of range value for column 'count' at row 1",t,re.I))
for i,m in enumerate(matches[-2:]):
 snippet=t[max(0,m.start()-750):m.end()+2000]
 # Safe only: identify column names and value tuple from the failing INSERT.
 first=re.search(r'INSERT\s+INTO\s+(.{0,120})',snippet,re.I|re.S)
 sample=''
 if first:
  sample=first.group(0)[:650]
  sample=re.sub(r'(?:(?<=\()|(?<=,))\s*\d+',lambda v:v.group().replace(v.group().strip(),'N'),sample)
  sample=re.sub(r'\d{2,}','N',sample)
  sample=re.sub(r"'[^']{10,}'","'[value]'",sample)
  sample=sample.replace('\n',' ')[:480]
 print('YML289_SQL_SHAPE='+json.dumps({'event':i,'first_insert_layout':sample,'masked_context':re.sub(r'[0-9]+','N',snippet[max(0,m.start()-140-max(0,m.start()-750)):m.start()-max(0,m.start()-750)+200]).replace('\n',' ')[:280]},ensure_ascii=False))
# Detect settings identifiers and repeated 14-digit category id without disclosing other SQL values.
needle='50277194631837'
for m in matches[-2:]:
 context=t[max(0,m.start()-4000):m.end()+5500]
 ix=context.find(needle)
 if ix>=0:
  before=context[max(0,ix-120):ix]
  after=context[ix+len(needle):ix+len(needle)+120]
  def shield(s):
   s=re.sub(r"'[^']+'","'[value]'",s)
   s=re.sub(r'\b\d+\b','N',s)
   return s.replace('\n',' ')[:120]
  print('YML289_LARGE_VALUE_CONTEXT='+json.dumps({'found':True,'before':shield(before),'after':shield(after),'near_values':re.findall(r'(?<!\d)(\d{10,})(?!\d)',context)[:15]},ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("YML289_"):print(line,flush=True)
if "YML289_SQL_SHAPE=" not in out:raise RuntimeError("No masked SQL shape available")
