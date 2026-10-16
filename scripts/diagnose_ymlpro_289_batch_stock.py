#!/usr/bin/env python3
"""Read-only diagnostic for YML PRO profile 289 batch-stock insert error."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE = r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
F="$(mktemp /tmp/yml289-error-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$sessions=$db->query('SELECT * FROM shop_yml_import_sessions WHERE profile_id=289 ORDER BY id DESC LIMIT 4')->fetchAll();
foreach($sessions as $session) {
 $out=array();
 foreach($session as $key=>$value) {
  if(preg_match('/(id|time|date|status|type|state|progress|error|result)/i',(string)$key)) {
   $out[$key]=is_scalar($value)?mb_substr((string)$value,0,100):gettype($value);
  }
 }
 echo 'YML289_SESSION='.json_encode($out,JSON_UNESCAPED_UNICODE).PHP_EOL;
}
$profile=$db->query('SELECT name,config FROM shop_yml_profiles WHERE id=289')->fetch();
if($profile) {
 $c=json_decode($profile['config'],true);
 echo 'YML289_PROFILE='.json_encode(array('name'=>$profile['name'],
  'mode'=>isset($c['images'])?$c['images']:null,
  'url_hostname'=>!empty($c['url'])?parse_url($c['url'],PHP_URL_HOST):null,
  'source_type'=>isset($c['source_type'])?$c['source_type']:null),JSON_UNESCAPED_UNICODE).PHP_EOL;
}
$tables=$db->query("SHOW TABLES")->fetchAll();
foreach($tables as $row) {
 $tbl=is_array($row)?reset($row):$row;
 if(stripos($tbl,'yml')===false || !preg_match('/(stock|batch|session|run)/i',$tbl))continue;
 $cols=$db->query('SHOW COLUMNS FROM `'.$tbl.'`')->fetchAll();
 $size=$db->query("SELECT TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='".$db->escape($tbl)."'")->fetch();
 $inds=$db->query('SHOW INDEX FROM \`'.$tbl.'\`')->fetchAll();
 $idx=array();
 foreach($inds as $x) {
  $name=$x['Key_name'];if(!isset($idx[$name]))$idx[$name]=array('unique'=>(int)$x['Non_unique']===0,'fields'=>array());
  $idx[$name]['fields'][]=$x['Column_name'];
 }
 $col=array();
 foreach($cols as $x)$col[]=array('name'=>$x['Field'],'type'=>$x['Type'],'null'=>$x['Null'],'key'=>$x['Key'],'default'=>is_null($x['Default'])?null:mb_substr((string)$x['Default'],0,40));
 echo 'YML289_TABLE='.json_encode(array('name'=>$tbl,'estimated_rows'=>(int)$size['TABLE_ROWS'],'data_bytes'=>(int)$size['DATA_LENGTH'],'indexes'=>$idx,'columns'=>$col),JSON_UNESCAPED_UNICODE).PHP_EOL;
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=1 '$F'" || echo YML289_PHP_AUDIT_FAILED=yes
python3 - <<'PY'
import os,re,json,pathlib,datetime
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
base=root/'wa-apps/shop/plugins/yml'
targets=[
 'lib/classes/import/batch/shopYmlBatchStockStorage.class.php',
 'lib/classes/import/batch/shopYmlBatchWriter.class.php',
 'lib/classes/import/handlers/shopYmlProductsHandler.class.php'
]
for target in targets:
 p=base/target
 if not p.is_file():continue
 s=p.read_text(encoding='utf-8',errors='replace')
 funcs=re.findall(r'function\s+([A-Za-z0-9_]+)\s*\(',s)
 markers=[(s[:m.start()].count('\n')+1,m.group()) for m in re.finditer(r'\b(?:INSERT\s+INTO|saveBatch|flush|insert|update|stock|duplicate)\b',s,re.I)]
 print('YML289_CODE='+json.dumps({'file':target,'functions':funcs[:50],'markers':markers[:50]},ensure_ascii=False))
# Only error text (not SQL parameters) and filenames from log files modified within 12 hours.
roots=[root/'wa-log',root/'wa-cache/logs']
files=[]
for directory in roots:
 if not directory.is_dir():continue
 for d,dirs,names in os.walk(str(directory)):
  dirs[:]=[x for x in dirs if x not in ('tmp','cache','assets')][:20]
  for name in names:
   path=pathlib.Path(d)/name
   try:stat=path.stat()
   except Exception:continue
   if stat.st_size>15000000 or stat.st_mtime<datetime.datetime.utcnow().timestamp()-3600*15:continue
   if path.suffix not in ('.log','.txt') and 'yml' not in name.lower():continue
   files.append(path)
  if len(files)>70:break
 if len(files)>70:break
candidates=[]
for p in files:
 try:txt=p.read_text(encoding='utf-8',errors='replace')
 except Exception:continue
 lines=txt.splitlines()
 selected=[]
 for ix,line in enumerate(lines):
  if not re.search(r'YmlBatch|batch.stock|yml.*(database|db|sql)|shop_yml_.*stock',line,re.I):continue
  if not re.search(r'(error|exception|duplicate|constraint|column|stock|batch)',line,re.I):continue
  nearby=lines[max(0,ix-3):min(len(lines),ix+4)]
  diag=[]
  for s in nearby:
   if re.search(r'(SQLSTATE|duplicate entry|column|data too long|constraint|error code|error\s*\d{3,5}|MySQL|row size|lock wait|deadlock|table.*full|BatchStock)',s,re.I):
    # omit SQL values, image URLs and sensitive data
    s=re.sub(r'(?i)(https?://)\S+',r'\1[redacted]',s)
    s=re.sub(r'(?i)(password|token|api_key)\s*[:=]\s*\S+',r'\1=[redacted]',s)
    s=re.sub(r'\b(?:INSERT|REPLACE|UPDATE|DELETE)\s+.*', '[SQL statement redacted]',s,flags=re.I)
    diag.append(s[:320])
  if diag:selected.append({'line':ix+1,'messages':diag[:4]})
 if selected:candidates.append({'log':str(p.relative_to(root)),'matches':selected[-7:]})
print('YML289_LOGS='+json.dumps(candidates[:18],ensure_ascii=False))
PY
"""
r=execute(REMOTE)
for line in r.splitlines():
 if line.startswith(("YML289_")):print(line,flush=True)
if "YML289_SESSION=" not in r:raise RuntimeError("No profile 289 session metadata")
