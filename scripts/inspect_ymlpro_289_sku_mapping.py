#!/usr/bin/env python3
"""Read-only examine why YML PRO created empty SKU codes for 33krovati."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE = r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
F="$(mktemp /tmp/yml289-skumap-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
function result($label,$arg) {echo $label.'='.json_encode($arg,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;}
$p=$db->query('SELECT name,config FROM shop_yml_profiles WHERE id=289')->fetch();
$c=json_decode($p['config'],true);
$top=array();
foreach($c as $k=>$v){
 $row=array('key'=>(string)$k,'type'=>gettype($v));
 if(is_array($v)){
  $row['count']=count($v);
  $row['keys']=array_map('strval',array_slice(array_keys($v),0,40));
 }elseif(is_scalar($v)&&preg_match('/(sku|code|vendor|product|offer|article|mapping|parse|setting|field|match|new|generate)/i',(string)$k)&&
   !preg_match('/(pass|token|key|auth|secret|url|file|source)/i',(string)$k)) {
  $row['safe_value']=(is_numeric($v)||$v==='')?$v:(strlen((string)$v)<40?$v:null);
 }
 $top[]=$row;
}
result('SKU289_CONFIG_STRUCTURE',$top);
$agg=$db->query("SELECT
 DATE(p.create_datetime) dt,
 COUNT(DISTINCT p.id) products,
 SUM(CASE WHEN s.sku IS NULL OR s.sku='' THEN 1 ELSE 0 END) blank,
 SUM(CASE WHEN s.sku IS NOT NULL AND s.sku<>'' THEN 1 ELSE 0 END) filled
 FROM shop_yml_products m
 JOIN shop_product p ON p.id=m.shop_id
 JOIN shop_product_skus s ON s.product_id=p.id
 WHERE m.profile_id=289
 GROUP BY DATE(p.create_datetime) ORDER BY dt DESC LIMIT 16")->fetchAll();
result('SKU289_CREATED_BY_DAY',$agg);
$examples=$db->query("SELECT p.id,p.create_datetime,m.yml_id,s.id sku_id,s.sku
 FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id
 JOIN shop_product_skus s ON s.product_id=p.id
 WHERE m.profile_id=289 AND s.sku IS NOT NULL AND s.sku<>''
 ORDER BY p.id DESC LIMIT 9")->fetchAll();
$brief=array();
foreach($examples as $x){
 $sku=(string)$x['sku'];
 $brief[]=array('product_id'=>(int)$x['id'],'sku_id'=>(int)$x['sku_id'],
  'sku_length'=>strlen($sku),'sku_prefix'=>substr($sku,0,min(8,strlen($sku))),
  'sku_matches_offer_id'=>$sku===(string)$x['yml_id'],
  'created'=>$x['create_datetime']);
}
result('SKU289_EXISTING_EXAMPLES',$brief);
$sum=$db->query("SELECT COUNT(DISTINCT m.shop_id) as p,
 SUM(CASE WHEN s.sku IS NULL OR s.sku='' THEN 1 ELSE 0 END) blank,
 SUM(CASE WHEN s.sku IS NOT NULL AND s.sku<>'' THEN 1 ELSE 0 END) filled
 FROM shop_yml_products m JOIN shop_product_skus s ON s.product_id=m.shop_id WHERE m.profile_id=289")->fetch();
result('SKU289_COUNTS',$sum);
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("SKU289_"):print(line,flush=True)
if 'SKU289_COUNTS=' not in out:raise RuntimeError('Missing SKU code diagnostic')
