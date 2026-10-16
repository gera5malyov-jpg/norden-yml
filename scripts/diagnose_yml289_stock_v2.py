#!/usr/bin/env python3
"""Read-only profile 289 stock INSERT diagnostic; no customer data or raw SQL values."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
F="$(mktemp /tmp/yml289-diagnostic-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
try {
 $rows=$db->query("SELECT TABLE_NAME,TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND (TABLE_NAME LIKE '%stock%' OR TABLE_NAME LIKE '%batch%') ORDER BY TABLE_NAME")->fetchAll();
 foreach($rows as $row){
  $tbl=$row['TABLE_NAME'];if(!preg_match('/^shop_[a-zA-Z0-9_]+$/',$tbl))continue;
  try{
   $cols=$db->query('SHOW COLUMNS FROM '.$tbl)->fetchAll();
   $keys=$db->query('SHOW INDEX FROM '.$tbl)->fetchAll();
   $columnData=array();
   foreach($cols as $x){$columnData[]=array('name'=>$x['Field'],'type'=>$x['Type'],'null'=>$x['Null'],'key'=>$x['Key'],'default'=>$x['Default']);}
   $keyData=array();
   foreach($keys as $x){
    $name=$x['Key_name'];if(!isset($keyData[$name]))$keyData[$name]=array('unique'=>(int)$x['Non_unique']===0,'columns'=>array());
    $keyData[$name]['columns'][]=$x['Column_name'];
   }
   echo 'YML289_STOCK_TABLE='.json_encode(array('table'=>$tbl,'row_estimate'=>(int)$row['TABLE_ROWS'],'size'=>(int)$row['DATA_LENGTH'],'columns'=>$columnData,'indexes'=>$keyData),JSON_UNESCAPED_UNICODE).PHP_EOL;
  }catch(Throwable $e){echo 'YML289_TABLE_ERROR='.json_encode(array('table'=>$tbl,'type'=>get_class($e),'reason'=>mb_substr($e->getMessage(),0,190)),JSON_UNESCAPED_UNICODE).PHP_EOL;}
 }
 foreach($db->query("SHOW VARIABLES WHERE Variable_name IN ('max_allowed_packet','innodb_strict_mode','sql_mode')")->fetchAll() as $v){
  echo 'YML289_SQL_VARIABLE='.json_encode(array('name'=>$v['Variable_name'],'value'=>mb_substr((string)$v['Value'],0,200)),JSON_UNESCAPED_UNICODE).PHP_EOL;
 }
}catch(Throwable $e){echo 'YML289_DB_METADATA_ERROR='.json_encode(array('type'=>get_class($e),'reason'=>mb_substr($e->getMessage(),0,150)),JSON_UNESCAPED_UNICODE).PHP_EOL;}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=1 '$F'" || echo YML289_PHP_FAILED=yes
python3 - <<'PY'
import os,re,json,pathlib,time
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
p=root/'wa-apps/shop/plugins/yml/lib/classes/import/batch/shopYmlBatchStockStorage.class.php'
s=p.read_text(encoding='utf-8',errors='replace')
table_names=sorted(set(re.findall(r'shop_[A-Za-z0-9_]*(?:stock|batch)[A-Za-z0-9_]*',s,re.I)))
print('YML289_STOCK_CLASS='+json.dumps({'tables':table_names,'insert_lines':[s.count(chr(10),0,m.start())+1 for m in re.finditer(r'INSERT\s+INTO',s,re.I)]},ensure_ascii=False))
logdir=root/'wa-log'
logs=[]
if logdir.is_dir():
 for d,dirs,names in os.walk(str(logdir)):
  dirs[:]=[x for x in dirs if not x.startswith('.')][:30]
  for name in names:
   path=pathlib.Path(d)/name
   try:st=path.stat()
   except Exception:continue
   if time.time()-st.st_mtime>15*3600:continue
   logs.append({'path':str(path.relative_to(root)),'size':st.st_size,'modified':time.strftime('%m-%d %H:%M UTC',time.gmtime(st.st_mtime))})
  if len(logs)>300:break
logs.sort(key=lambda x:x['modified'],reverse=True)
print('YML289_LOG_INVENTORY='+json.dumps(logs[:65],ensure_ascii=False))
# Return only recognized database error codes, never full logged SQL or its values.
error_re=re.compile(r'SQLSTATE\[[0-9A-Z]+\]|(?:Duplicate entry|Data too long|Column count|Out of range value|Unknown column|Foreign key constraint|Cannot add or update|Packet bigger than|Lock wait timeout|Deadlock found|Incorrect integer|Truncated incorrect|Table .* full)',re.I)
for info in logs:
 path=root/info['path']
 if info['size']>65000000:continue
 try:txt=path.read_text(encoding='utf-8',errors='replace')[-7000000:]
 except Exception:continue
 errors=[]
 for i,line in enumerate(txt.splitlines()):
  m=error_re.search(line)
  if not m:continue
  snippet=line[m.start():m.start()+180]
  snippet=re.sub(r'https?://\S+','[url]',snippet)
  snippet=re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\b','[email]',snippet)
  snippet=re.split(r'(?i)\s+(?:INSERT|UPDATE|DELETE|REPLACE)\s+',snippet)[0]
  errors.append({'line':i+1,'summary':snippet[:170]})
 if errors:print('YML289_LOG_ERRORS='+json.dumps({'path':info['path'],'errors':errors[-12:]},ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("YML289_"):print(line,flush=True)
if "YML289_STOCK_CLASS=" not in out:raise RuntimeError("Missing stock diagnostic report")
