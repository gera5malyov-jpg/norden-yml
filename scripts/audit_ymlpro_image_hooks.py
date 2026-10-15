#!/usr/bin/env python3
"""Read-only hook and import-profile architecture check, no code or data exported."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r'''set -euo pipefail
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
base=root/'wa-apps/shop/plugins/yml'
files={
 'images':base/'lib/classes/import/shopYmlImages.class.php',
 'processor':base/'lib/classes/import/processors/shopYmlProductDataProcessor.class.php',
 'handler':base/'lib/classes/import/handlers/shopYmlProductsHandler.class.php',
 'plugin':base/'lib/shopYml.plugin.php',
}
def get(f):return f.read_text(encoding='utf-8',errors='replace') if f.is_file() else ''
for label,p in files.items():
 s=get(p)
 funcs=re.findall(r'\b(?:public|private|protected)?\s*(?:static\s+)?function\s+([a-zA-Z0-9_]+)\s*\(([^)]{0,500})\)',s)
 funcs=[{'method':name,'arg_count':len([x for x in args.split(',') if '$' in x]),'arg_names':re.findall(r'\$([A-Za-z_][A-Za-z_0-9]*)',args)[:8]} for name,args in funcs if re.search('image|picture|download|product|queue|insert|setImage|import|process|finish',name,re.I)]
 print('YMLDETAIL_SIGNATURES_'+label.upper()+'='+json.dumps(funcs[:45],ensure_ascii=False))
 if label=='images':
  # Extract event literal only, classify path execution by action order, no code fragments.
  events=re.findall(r'(?:->event|->getEvent|->callEvent)\s*\(\s*([\'\"][^\'\"]+[\'\"])',s)
  print('YMLDETAIL_EVENTS='+json.dumps(events[:30],ensure_ascii=False))
  markers=['queueImages','parseImages','shouldImportImages','setImage','processDownloadedImage','insertImage','flushPendingInserts']
  order=[]
  for marker in markers:
   m=re.search(r'function\s+'+re.escape(marker)+r'\s*\(',s)
   if not m:continue
   b=s[m.end():]
   n=re.search(r'\n\s*(?:(?:public|private|protected)\s+)?(?:static\s+)?function\s+',b)
   block=b[:n.start()] if n else b[:4500]
   keys=[]
   for key,expr in [('dispatch_event',r'->event\s*\('),('db_insert',r'->insert\s*\('),('db_exec',r'->exec\s*\('),('remote_fetch',r'curl_exec|->download\s*\(|file_get_contents\s*\('),('set_product_summary',r'\bsummary\b'),('queue_push',r'\$this->\w*queue\w*'),('local_move',r'waFiles::move|rename\s*\('),('save_media',r'shopProductImagesModel|generateThumbs|waImage')]:
    for ma in re.finditer(expr,block,re.I):keys.append([ma.start(),key])
   order.append({'method':marker,'operations':[x[1] for x in sorted(keys)[:24]],'returns_count':len(re.findall(r'\breturn\b',block))})
  print('YMLDETAIL_IMAGE_LIFECYCLE='+json.dumps(order,ensure_ascii=False))
 if label=='processor':
  maps=[x for x in re.findall(r'\b(?:image|picture|summary|description|product_id|sku|offer_id|vendor_code|external_id)\b',s,re.I)]
  print('YMLDETAIL_DATA_KEYS='+json.dumps({k:maps.count(k) for k in dict.fromkeys(maps)},ensure_ascii=False))
# DB schema only (not rows)
sqlschema=root/'wa-apps/shop/plugins/yml/lib/config/db.php'
if sqlschema.is_file():
 t=get(sqlschema)
 tables=list(dict.fromkeys(re.findall(r"""['"](shop_[a-z0-9_]*yml[a-z0-9_]*)['"]""",t,re.I)))
 print('YMLDETAIL_TABLE_NAMES='+json.dumps(tables[:40],ensure_ascii=False))
else: print('YMLDETAIL_TABLE_NAMES=[]')
# read-only local PHP metadata of configured YML profile count by querying SHOW TABLES and SELECT COUNT only.
PY'''
result=execute(REMOTE)
for line in result.splitlines():
 if line.startswith('YMLDETAIL_'):print(line,flush=True)
if 'YMLDETAIL_EVENTS=' not in result:raise RuntimeError('Missing structure report')
