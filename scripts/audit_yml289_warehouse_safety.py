#!/usr/bin/env python3
"""Read-only stock-setting safety audit of 33krovati profile 289."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'storefront'))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml289-stock-safety-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat > "$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
try {
 $p=$db->query('SELECT name,config FROM shop_yml_profiles WHERE id=289')->fetch();
 $c=json_decode($p['config'],true);
 $desired=array('stock','reset_stocks','reset_all_stocks','default_stock_mode','default_stock_value',
   'default_stock_scope','default_available_scope','default_available_value','disappeared_action',
   'disappeared_reset_mode','disappeared_wipe_guard_pct','warehouse','stock_id');
 $vals=array();
 foreach($desired as $key)if(array_key_exists($key,$c))$vals[$key]=$c[$key];
 echo 'YMLSTOCK_SETTINGS='.json_encode($vals,JSON_UNESCAPED_UNICODE).PHP_EOL;
 $stockCols=$db->query('SHOW COLUMNS FROM shop_product_stocks')->fetchAll();
 $cols=array_map(function($row){return $row['Field'];},$stockCols);
 echo 'YMLSTOCK_TABLE_COLUMNS='.json_encode($cols).PHP_EOL;
 $stats=$db->query("SELECT COUNT(*) sku_rows, SUM(s.count=0) sku_zero,SUM(s.count>0) sku_positive,
  SUM(s.count IS NULL) sku_unlimited,SUM(s.available=1) available_sku,
  MIN(s.count) min_count,MAX(s.count) max_count
 FROM shop_yml_products yp JOIN shop_product_skus s ON s.product_id=yp.shop_id WHERE yp.profile_id=289")->fetch();
 echo 'YMLSTOCK_SKU='.json_encode($stats,JSON_UNESCAPED_UNICODE).PHP_EOL;
 $st=$db->query("SELECT ps.stock_id,COUNT(*) records,SUM(ps.count=0) zero_count,SUM(ps.count>0) positive_count,
 SUM(ps.count IS NULL) unlimited_count,MAX(ps.count) max_count
 FROM shop_yml_products yp JOIN shop_product_skus s ON s.product_id=yp.shop_id
 JOIN shop_product_stocks ps ON ps.sku_id=s.id WHERE yp.profile_id=289
 GROUP BY ps.stock_id ORDER BY ps.stock_id")->fetchAll();
 echo 'YMLSTOCK_WAREHOUSES='.json_encode($st,JSON_UNESCAPED_UNICODE).PHP_EOL;
 try{
 $stockNames=$db->query("SELECT id,name FROM shop_stock ORDER BY id")->fetchAll();
 echo 'YMLSTOCK_STOCK_NAMES='.json_encode($stockNames,JSON_UNESCAPED_UNICODE).PHP_EOL;
 }catch(Throwable $e){}
 $ses=$db->query("SELECT id,status,start_datetime,end_datetime FROM shop_yml_import_sessions WHERE profile_id=289 ORDER BY id DESC LIMIT 4")->fetchAll();
 echo 'YMLSTOCK_SESSIONS='.json_encode($ses,JSON_UNESCAPED_UNICODE).PHP_EOL;
}catch(Throwable $e){echo 'YMLSTOCK_ERROR='.get_class($e).' '.substr($e->getMessage(),0,120).PHP_EOL;}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith('YMLSTOCK_'):print(line,flush=True)
if 'YMLSTOCK_SKU=' not in out:raise RuntimeError('Warehouse stock audit incomplete')
