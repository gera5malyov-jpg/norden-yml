#!/usr/bin/env python3
"""Read-only numerical forensic checks for YML PRO 289 stock overflow."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml-stock-config-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$p=$db->query('SELECT config FROM shop_yml_profiles WHERE id=289')->fetch();
$c=json_decode($p['config'],true);
$out=array();
$iter=function($node,$prefix)use(&$out,&$iter){
 if (!is_array($node))return;
 foreach($node as $key=>$value){
  $path=($prefix===''?'':$prefix.'.').(string)$key;
  if(preg_match('/(stock|count|quant|amount|available|infinite|warehous|остат|налич)/i',$path)){
   if(is_array($value)){$out[$path]=array('type'=>'array','size'=>count($value));}
   else if(is_bool($value)||is_numeric($value)||$value===null){$out[$path]=$value;}
   else if(is_string($value) && mb_strlen($value)<60 && !preg_match('/(https?:|@|token|password)/i',$value)){$out[$path]=$value;}
  }
  if(is_array($value) && strlen($path)<75)$iter($value,$path);
 }
};
$iter($c,'');
echo 'YML289_STOCK_SETTINGS='.json_encode($out,JSON_UNESCAPED_UNICODE).PHP_EOL;
$associations=$db->query('SELECT * FROM shop_yml_stock_values WHERE profile_id=289')->fetchAll();
foreach($associations as $a) {
 echo 'YML289_STOCK_MAPPING='.json_encode(array('old_value'=>substr((string)$a['old_value'],0,35),'new_value'=>$a['new_value'],'infinite'=>$a['is_infinite']),JSON_UNESCAPED_UNICODE).PHP_EOL;
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
p=root/'wa-log/db.log'
with p.open('rb') as f:
 f.seek(max(0,p.stat().st_size-14000000))
 t=f.read().decode('utf-8','replace')
errors=list(re.finditer("Out of range value for column 'count' at row 1",t,re.I))
print('YML289_OVERFLOW_COUNT='+str(len(errors)))
for m in errors[-6:]:
 before=t[max(0,m.start()-10000):m.start()]
 after=t[m.end():m.end()+8000]
 chunk=before[-6000:]+t[m.start():m.end()]+after[:6000]
 sql=list(re.finditer(r'INSERT\s+INTO\s+([^\s(]+)',chunk,re.I))
 nums=re.findall(r'(?<![A-Za-z])(?:[+-]?\d{11,}|[+-]?\d+\.\d+[eE][+-]?\d+|[+-]?\d+[eE][+-]?\d+)(?![A-Za-z])',chunk)
 # Keep only distinct numerical facts, no customer/order fields or full SQL.
 values=sorted(set(nums),key=lambda x:-len(x))
 large=[x for x in values if len(x)>=10][:25]
 line_no=t[:m.start()].count(chr(10))+1
 print('YML289_OVERFLOW_EVENT='+json.dumps({'tail_line':line_no,'sql_table_mentions':[x.group(1).strip(chr(96)) for x in sql[-4:]],'large_numeric_literals':large[:20],'context_labels':list(dict.fromkeys(re.findall(r'(?:SQLSTATE|Error #\d+|Query:|INSERT INTO|shop_product_stocks|shop_product_skus)',chunk,re.I)))},ensure_ascii=False))
src=root/'wa-apps/shop/plugins/yml/lib/classes/import/batch/shopYmlBatchStockStorage.class.php'
s=src.read_text(encoding='utf-8',errors='replace')
tokens=['INF','PHP_INT_MAX','max','count','intval','floatval','NULL','CONVERT','9999999','INSERT INTO','ON DUPLICATE']
print('YML289_STOCK_CODE_FLAGS='+json.dumps({key:len(re.findall(re.escape(key),s,re.I)) for key in tokens}))
PY
"""
r=execute(REMOTE)
for line in r.splitlines():
 if line.startswith("YML289_"):print(line,flush=True)
if "YML289_OVERFLOW_COUNT=" not in r:raise RuntimeError("No MySQL numeric forensic output")
