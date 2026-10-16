#!/usr/bin/env python3
"""Read-only inspection of YML PRO settings controlling absent product stock."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r'''set -euo pipefail
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml')
include_exts={'.html','.tpl','.js','.php','.json'}
words=re.compile(r'(отсутств|нет в (?:файле|выгрузке|каталоге)|недоступн|не найден|не найденн|неактуал|обнуля|остатк|наличи|stock|disappear|missing|absence|not_found|out.of.stock|zero.stock|remove.missing|not.?present)',re.I)
hits=[]
for f in root.rglob('*'):
 if not f.is_file() or f.suffix.lower() not in include_exts or f.stat().st_size>400000:continue
 p=str(f.relative_to(root))
 if '/vendor/' in p or '/vendors/' in p or '/classes/parser/' in p:continue
 try:s=f.read_text(encoding='utf-8',errors='replace')
 except Exception:continue
 ll=s.splitlines()
 for ix,line in enumerate(ll):
  if not words.search(line):continue
  if len(line)>1600:continue
  before=' '.join(ll[max(0,ix-2):ix])
  after=' '.join(ll[ix+1:min(len(ll),ix+3)])
  # emit only setting identifiers and localized labels, not licensed source code
  keys=re.findall(r'(?:(?:name|id|data-name|data-key|for)\s*=\s*["\']([^"\']{1,120})["\']|\$[a-z_]+\[(?:["\'])([a-z_]+)(?:["\'])\])',before+' '+line+' '+after,re.I)
  identifiers=list(dict.fromkeys(a or b for a,b in keys))
  if f.suffix in ('.html','.tpl','.js'):
   labels=re.findall(r'[А-Яа-яЁё][^<>"{}]{4,130}',line)
   short=[x.strip()[:120] for x in labels[:2]]
  else:short=[]
  hits.append({'file':p,'line':ix+1,'settings':identifiers[:6],'labels':short[:3],
     'word':words.search(line).group().lower(),'nearby_keys':list(dict.fromkeys(re.findall(r"(?:missing|stock|disappear|absent|remove|delete|quantity|zero|not_found|ignore|exclude|drop|disabled|deactivate)[a-zA-Z0-9_]*",before+' '+line+' '+after,re.I)))[:8]})
# Sort UI first
hits.sort(key=lambda v:(not v['file'].startswith('templates/'),not v['file'].endswith(('.html','.tpl','.js')),v['file'],v['line']))
print('YML_ABSENT_SCAN='+json.dumps(hits[:190],ensure_ascii=False))
print('YML_ABSENT_TOTAL='+str(len(hits)))
PY'''
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("YML_ABSENT_"):print(line,flush=True)
if 'YML_ABSENT_SCAN=' not in out:raise RuntimeError("Audit incomplete")
