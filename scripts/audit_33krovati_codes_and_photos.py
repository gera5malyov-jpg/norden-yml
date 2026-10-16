#!/usr/bin/env python3
"""Read-only live diagnostics for YML PRO profile 289 product codes and extimg."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml289-codes-images-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$id=289;
$p=$db->query('SELECT id,name,config FROM shop_yml_profiles WHERE id=289')->fetch();
$c=json_decode($p['config'],true);
if(!is_array($c))throw new RuntimeException('Invalid YML profile config');
$settings=array();
$walk=function($v,$path,$depth)use(&$walk,&$settings){
 if($depth>4 || count($settings)>90)return;
 foreach($v as $k=>$value){
  $key=(string)$k;$next=$path.'/'.$key;
  if(preg_match('/pass|secret|login|auth|token|api|cookie|header|credential/i',$next))continue;
  if(is_array($value)){$walk($value,$next,$depth+1);continue;}
  if(preg_match('/sku|code|offer|artic|vendor|артик|код|generate|create|url|field|mapping|image|picture|photo|name|source_type|file/i',$next)){
   $settings[]=array('key'=>substr($next,0,95),
    'value'=>preg_match('/url|file/i',$next)?(empty($value)?'empty':'set'):((is_scalar($value) && strlen((string)$value)<100)?$value:'not_displayed'));
  }
 }
};
$walk($c,'',0);
$mapping=$db->query("SELECT COUNT(*) total, COUNT(DISTINCT m.shop_id) distinct_products,
 SUM(p.id IS NULL) missing_product, SUM(p.summary LIKE '%[extimg]%') with_external,
 SUM(p.image_id>0) native, SUM(p.url IS NULL OR p.url='') empty_url,
 SUM(p.status=1) published
 FROM shop_yml_products m LEFT JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id=289")->fetch();
$sku=$db->query("SELECT COUNT(*) rows_total, COUNT(DISTINCT s.product_id) products_with_sku,
 SUM(s.sku IS NULL OR s.sku='') blank_sku,
 SUM(s.name IS NULL OR s.name='') blank_sku_name
 FROM shop_product_skus s JOIN (SELECT DISTINCT shop_id FROM shop_yml_products WHERE profile_id=289) m ON m.shop_id=s.product_id")->fetch();
$samples=$db->query("SELECT m.shop_id,p.url,p.summary,s.sku,s.id sku_id
 FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id
 LEFT JOIN shop_product_skus s ON s.product_id=m.shop_id
 WHERE m.profile_id=289 ORDER BY m.shop_id DESC LIMIT 12")->fetchAll();
$out=array();
foreach($samples as $x) {
 preg_match_all('~\[extimg\](.*?)\[/extimg\]~is',(string)$x['summary'],$pic);
 $out[]=array('product_id'=>(int)$x['shop_id'],'sku_set'=>(string)$x['sku']!=='',
 'sku_numeric'=>preg_match('/^[0-9]+$/',(string)$x['sku'])===1,
 'has_product_url'=>!empty($x['url']),'extimg_blocks'=>count($pic[1]),'sku_id'=>(int)$x['sku_id']);
}
$sessions=$db->query('SELECT id,start_datetime,end_datetime,status,exec_type,error_message FROM shop_yml_import_sessions WHERE profile_id=289 ORDER BY id DESC LIMIT 6')->fetchAll();
$state=array('name'=>$p['name'],'id'=>289,'has_url'=>!empty($c['url']),
 'source_host'=>!empty($c['url'])?parse_url($c['url'],PHP_URL_HOST):null,
 'source_type'=>isset($c['source_type'])?$c['source_type']:null,
 'images_mode'=>isset($c['images'])?$c['images']:null,
 'mapped'=>$mapping,'skus'=>$sku,'samples'=>$out,'settings'=>$settings,'sessions'=>$sessions);
echo 'YML289_STATE='.json_encode($state,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=1 '$F'"
python3 - <<'PY'
import pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
log=root/'wa-log/shop/plugins/univimport/photo-sync.log'
if log.is_file():
 lines=log.read_text(encoding='utf-8',errors='replace').splitlines()
 entries=[l[:420] for l in lines if 'PHOTO_PROFILE=' in l and ('"id":289,' in l or '"id":289}' in l)]
 print('YML289_PHOTO_CRON='+json.dumps(entries[-5:],ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith('YML289_'):print(line,flush=True)
if 'YML289_STATE=' not in out:raise RuntimeError('No YML 289 diagnostic result')
