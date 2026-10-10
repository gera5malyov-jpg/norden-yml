#!/usr/bin/env python3
"""Stage 20: read-only conversion health and manufacturer catalog audit.

Reads aggregate Shop-Script product, order, and feature health. No private records or writes.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
p=$(mktemp /tmp/storefront-conversion-audit-XXXXXXXX.php)
trap 'rm -f "$p"' EXIT
cat >"$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$out=array();
function read_rows($m,$sql){try{return $m->query($sql)->fetchAll();}catch(Throwable $e){return array('error'=>$e->getMessage());}}
function read_val($m,$sql){try{return (int)$m->query($sql)->fetchField();}catch(Throwable $e){return null;}}
$tables=read_rows($m,'SHOW TABLES');
$names=array();
foreach($tables as $row){foreach($row as $name){$names[]=$name;}}
$out['matching_tables']=array_values(array_filter($names,function($x){return preg_match('/brand|checkout|shipping|payment|search|cart|feature|plugin/',$x);}));
foreach(array('shop_product','shop_product_skus','shop_feature','shop_product_features','shop_feature_values_varchar','shop_category','shop_order','shop_order_params') as $table){
 $columns=read_rows($m,'SHOW COLUMNS FROM '.$table);
 $cols=array();
 foreach($columns as $row){if(isset($row['Field'])){$cols[]=$row['Field'];}}
 $out['columns'][$table]=$cols;
}
$out['products']=array(
  'total'=>read_val($m,'SELECT COUNT(*) FROM shop_product'),
  'active'=>read_val($m,'SELECT COUNT(*) FROM shop_product WHERE status=1'),
  'no_main_photo'=>read_val($m,'SELECT COUNT(*) FROM shop_product WHERE status=1 AND (image_id=0 OR image_id IS NULL)'),
  'without_category'=>read_val($m,'SELECT COUNT(*) FROM shop_product WHERE status=1 AND (category_id=0 OR category_id IS NULL)'),
  'without_description'=>read_val($m,"SELECT COUNT(*) FROM shop_product WHERE status=1 AND (description IS NULL OR TRIM(description)='')"),
  'without_summary'=>read_val($m,"SELECT COUNT(*) FROM shop_product WHERE status=1 AND (summary IS NULL OR TRIM(summary)='')"),
  'in_stock'=>read_val($m,'SELECT COUNT(*) FROM shop_product WHERE status=1 AND (count>0 OR count IS NULL)'),
  'out_of_stock'=>read_val($m,'SELECT COUNT(*) FROM shop_product WHERE status=1 AND count=0'),
  'no_price'=>read_val($m,'SELECT COUNT(*) FROM shop_product WHERE status=1 AND price<=0')
);
$out['feature_candidates']=read_rows($m,"SELECT id,code,name,type,status FROM shop_feature WHERE code LIKE '%brand%' OR code LIKE '%manufactur%' OR code LIKE '%proizvod%' OR name LIKE '%Бренд%' OR name LIKE '%роизводител%' ORDER BY id LIMIT 20");
$out['brand_like_tables']=array_values(array_filter($names,function($x){return preg_match('/brand|manufacturer/',$x);}));
$out['checkout']=array(
  'active_shipping_plugins'=>read_rows($m,"SELECT plugin,value FROM shop_plugin_settings WHERE name='enabled' LIMIT 40"),
  'order_columns'=>$out['columns']['shop_order']
);
$out['manufacturer_counts']=read_rows($m,"SELECT f.feature_id, COUNT(DISTINCT f.product_id) AS total_products FROM shop_product_features f INNER JOIN shop_product p ON p.id=f.product_id WHERE f.feature_id IN (9,568,2329) AND p.status=1 GROUP BY f.feature_id");
$out['manufacturer_values']=read_rows($m,"SELECT v.value,COUNT(DISTINCT f.product_id) AS active_products FROM shop_feature_values_varchar v INNER JOIN shop_product_features f ON f.feature_value_id=v.id AND f.feature_id=9 INNER JOIN shop_product p ON p.id=f.product_id AND p.status=1 WHERE v.feature_id=9 AND TRIM(v.value)!='' GROUP BY v.id,v.value ORDER BY active_products DESC LIMIT 110");
$out['manufacturer_value_count']=read_val($m,"SELECT COUNT(*) FROM shop_feature_values_varchar WHERE feature_id=9");
$out['information_pages']=read_rows($m,"SELECT id,name,title,url,full_url,domain,route,status,CHAR_LENGTH(content) AS content_size,LEFT(content,350) AS text_intro FROM shop_page WHERE domain='profikompany.ru' AND route='*' AND (url LIKE '%garant%' OR url LIKE '%vozmozh%' OR url LIKE '%o-kompan%' OR url LIKE '%proizvod%') LIMIT 25");
$out['shop_settings_featured']=read_rows($m,"SELECT app_id,name,CHAR_LENGTH(value) AS value_size FROM wa_app_settings WHERE app_id='shop' AND (name LIKE '%checkout%' OR name LIKE '%search%' OR name LIKE '%rating%') LIMIT 45");
$out['eligible_instock_categorized']=read_rows($m,"SELECT COUNT(*) AS products,
 SUM(CASE WHEN (description IS NULL OR TRIM(description)='') THEN 1 ELSE 0 END) AS no_full_description,
 SUM(CASE WHEN (summary IS NULL OR TRIM(summary)='') THEN 1 ELSE 0 END) AS no_short_description,
 SUM(CASE WHEN (image_id IS NULL OR image_id=0) THEN 1 ELSE 0 END) AS no_internal_main_image,
 SUM(CASE WHEN (image_id IS NULL OR image_id=0) AND (summary LIKE '%http%' OR description LIKE '%http%') THEN 1 ELSE 0 END) AS no_internal_main_image_but_external_link_hint,
 SUM(CASE WHEN price<=0 THEN 1 ELSE 0 END) AS zero_price,
 SUM(CASE WHEN (meta_title IS NULL OR meta_title='') THEN 1 ELSE 0 END) AS no_manual_meta_title
 FROM shop_product WHERE status=1 AND category_id>0 AND (count>0 OR count IS NULL)");
$out['top_categories_quality']=read_rows($m,"SELECT c.id,c.name,COUNT(*) AS active_instock,
 SUM(CASE WHEN (p.description IS NULL OR TRIM(p.description)='') THEN 1 ELSE 0 END) AS no_description,
 SUM(CASE WHEN p.image_id=0 OR p.image_id IS NULL THEN 1 ELSE 0 END) AS no_internal_main_image,
 SUM(CASE WHEN p.price<=0 THEN 1 ELSE 0 END) AS zero_price
 FROM shop_product p JOIN shop_category c ON c.id=p.category_id
 WHERE p.status=1 AND p.category_id>0 AND (p.count>0 OR p.count IS NULL)
 GROUP BY c.id,c.name ORDER BY active_instock DESC LIMIT 18");
$out['checkout_flow_schema']=read_rows($m,"SHOW COLUMNS FROM shop_checkout_flow");
$out['plugin_settings_schema']=read_rows($m,"SHOW COLUMNS FROM shop_plugin_settings");
$out['shop_plugin_schema']=read_rows($m,"SHOW COLUMNS FROM shop_plugin");
$out['order_param_names']=read_rows($m,"SELECT name,COUNT(*) AS instances FROM shop_order_params WHERE name LIKE '%storefront%' OR name LIKE '%referer%' OR name LIKE '%shipping%' GROUP BY name ORDER BY instances DESC LIMIT 30");
$out['top_types']=read_rows($m,"SELECT type_id,COUNT(*) AS total FROM shop_product WHERE status=1 GROUP BY type_id ORDER BY total DESC LIMIT 12");
$out['recent_orders_by_state']=read_rows($m,"SELECT state_id,COUNT(*) AS orders FROM shop_order WHERE create_datetime >= DATE_SUB(NOW(),INTERVAL 30 DAY) GROUP BY state_id ORDER BY orders DESC LIMIT 15");
echo 'CONVERSION_ADMIN_AUDIT='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php -d display_errors=1 -d log_errors=0 '$p'"
"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-stage20-conversion-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage20-conversion-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('CONVERSION_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
