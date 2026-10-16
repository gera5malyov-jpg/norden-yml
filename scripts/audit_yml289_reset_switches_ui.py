#!/usr/bin/env python3
"""Read-only inspect YML PRO 'reset stocks' selector options, labels only."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
python3 - <<'PY'
from pathlib import Path
import re,json
p=Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml/templates/actions/profile/tabs/product-tabs/stock.html')
lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
out=[]
for i in range(58,min(len(lines),248)):
 line=lines[i].strip()
 if re.search('reset_|disappeared|stock|склад|Выбран|Сохран|Обнул|Устан|Все ск|Префикс|пропа|отсутств|hidden|radio|checkbox',line,re.I):
  keys=re.findall(r'(?:settings|profile)->([A-Za-z_]\w*)|name=["\x27]([\w\[\]\.-]+)',line)
  clean=re.sub(r'<[^>]*>',' ',line)
  clean=re.sub(r'\{[^{}]*\}',' ',clean)
  clean=re.sub(r'\s+',' ',clean).strip()
  out.append({'line':i+1,'label':clean[:160],'key_names':[x or y for x,y in keys][:4]})
print('YMLSTOCK_UI='+json.dumps(out[:100],ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith('YMLSTOCK_UI='):print(line,flush=True)
if 'YMLSTOCK_UI=' not in out:raise RuntimeError('Missing UI')
