#!/usr/bin/env python3
"""Read-only current inventory and SKU-code diagnostics for YML PRO profile 289."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml-code-stock-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$pid=289;
try{
 $map=(int)$db->query('SELECT COUNT(DISTINCT shop_id) FROM shop_yml_products WHERE profile_id='.$pid)->fetchField();
 $n=(int)$db->query("SELECT COUNT(DISTINCT yp.shop_id) FROM shop_yml_products yp JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=289 AND p.count=0")->fetchField();
 $null=(int)$db->query("SELECT COUNT(DISTINCT yp.shop_id) FROM shop_yml_products yp JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=289 AND p.count IS NULL")->fetchField();
 $sku=$db->query("SELECT COUNT(*) total, SUM(s.sku IS NULL OR s.sku='') blank_code, COUNT(DISTINCT s.product_id) products FROM shop_yml_products yp JOIN shop_product_skus s ON yp.shop_id=s.product_id WHERE yp.profile_id=289")->fetch();
 $codes=$db->query("SELECT s.product_id,CHAR_LENGTH(s.sku) code_len,CHAR_LENGTH(s.name) sku_name_len FROM shop_yml_products yp JOIN shop_product_skus s ON s.product_id=yp.shop_id WHERE yp.profile_id=289 ORDER BY s.product_id DESC LIMIT 10")->fetchAll();
 echo 'YML289_CURRENT='.json_encode(array('mapped'=>$map,'zero_aggregate'=>$n,'unlimited_aggregate'=>$null,'sku_stats'=>$sku,'recent_sku_code_lengths'=>$codes),JSON_UNESCAPED_UNICODE).PHP_EOL;
 $row=$db->query('SELECT * FROM shop_yml_import_sessions WHERE profile_id=289 ORDER BY id DESC LIMIT 1')->fetch();
 if($row){
  $cols=array();
  foreach($row as $key=>$value){
   if(preg_match('/(report|stat|result|counter|progress|disappeared|zero|stock)/i',$key)){
    if(is_scalar($value)&&strlen((string)$value)<7000){
     $decoded=json_decode($value,true);
     if(is_array($decoded)) {
      $flat=array();
      foreach($decoded as $subkey=>$v)if(preg_match('/(disappear|zero|count|stock|sku|code|product)/i',$subkey))$flat[$subkey]=is_scalar($v)?$v:gettype($v);
      $cols[$key]=array('keys'=>array_keys($decoded),'flat'=>$flat);
     }else $cols[$key]=mb_substr((string)$value,0,150);
    }else $cols[$key]=gettype($value);
   }
  }
  echo 'YML289_LATEST_SESSION='.json_encode(array('id'=>(int)$row['id'],'status'=>$row['status'],'fields'=>$cols),JSON_UNESCAPED_UNICODE).PHP_EOL;
 }
 echo 'YML289_PRODUCT_COLUMNS='.json_encode(array_map(function($x){return $x['Field'];},$db->query('SHOW COLUMNS FROM shop_product')->fetchAll()),JSON_UNESCAPED_UNICODE).PHP_EOL;
}catch(Throwable $e){echo 'YML289_CURRENT_ERROR='.get_class($e).' '.mb_substr($e->getMessage(),0,90).PHP_EOL;}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml/templates/actions/profile/tabs/product-tabs')
for name in ['identification.html','stock.html','mapper.html']:
 p=root/name
 if not p.is_file():continue
 lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
 out=[]
 for i,line in enumerate(lines):
  if re.search('код товара|код артикула|созда|генер|префикс|идентификац|исчезнувшие|обнулять|защита от обнуления|reset_stocks|stock_mode',line,re.I):
   clean=re.sub(r'<[^>]*>',' ',line)
   clean=re.sub(r'\{[^{}]*\}',' ',clean)
   clean=re.sub(r'\s+',' ',clean).strip()
   if clean:out.append({'line':i+1,'label':clean[:130]})
 print('YML289_UI_PART='+json.dumps({'name':name,'labels':out[:55]},ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith('YML289_'):print(line,flush=True)
if 'YML289_CURRENT=' not in out:raise RuntimeError('Current profile stock and SKU report unavailable')
