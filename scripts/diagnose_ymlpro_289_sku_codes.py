#!/usr/bin/env python3
"""Read-only diagnosis of SKU code auto-creation for YML PRO profile 289."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml289-sku-audit-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
function emitted($name,$value) {echo $name.'='.json_encode($value,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;}
function sqlvalue($db,$sql) {try{return $db->query($sql)->fetch();}catch(Throwable $e){return array('_sql_error'=>get_class($e).' '.substr($e->getMessage(),0,110));}}
foreach(array('shop_product','shop_product_skus','shop_yml_products','shop_yml_profiles','shop_yml_import_sessions') as $table){
 try {
  $columns=$db->query('SHOW COLUMNS FROM '.$table)->fetchAll();
  emitted('SKU289_SCHEMA',array('table'=>$table,'columns'=>array_map(function($c){return $c['Field'];},$columns)));
 }catch(Throwable $e){emitted('SKU289_SCHEMA_ERROR',array('table'=>$table,'class'=>get_class($e)));}
}
$p=$db->query('SELECT name,config FROM shop_yml_profiles WHERE id=289')->fetch();
if($p){
 $cfg=json_decode($p['config'],true);
 emitted('SKU289_PROFILE_IDENTITY',array('name'=>$p['name'],'config_valid'=>is_array($cfg)));
 if(is_array($cfg)){
  $out=array();$queue=array(array('',$cfg));
  while($queue){
   $pair=array_shift($queue);
   foreach($pair[1] as $key=>$val){
    $name=$pair[0].'/'.$key;
    if(preg_match('/sku|vendor|code|article|product|offer|import|generator|auto|mode|key|field|id|update|new|create|sync/i',(string)$key) &&
       !preg_match('/pass|secret|auth|api|token|private|user|url|link|path/i',(string)$key)){
       $out[]=array('key'=>substr($name,0,120),'type'=>gettype($val),
          'value'=>(is_bool($val)||is_int($val)||is_float($val)||($val===null))?$val:
           (is_string($val)&&strlen($val)<34&&!preg_match('~[/@]|https?~i',$val)?$val:null));
    }
    if(is_array($val)&&count($queue)<100&&substr_count($name,'/')<4)$queue[]=array($name,$val);
   }
  }
  emitted('SKU289_CONFIG_OPTIONS',array_slice($out,0,120));
 }
}
$schema=$db->query("SHOW COLUMNS FROM shop_product_skus")->fetchAll();
$fields=array();
foreach($schema as $c)$fields[]=$c['Field'];
if(in_array('sku',$fields,true)){
 $summary=sqlvalue($db,"SELECT COUNT(DISTINCT yp.shop_id) products,COUNT(*) sku_rows,
 SUM(CASE WHEN s.sku IS NULL OR TRIM(s.sku)='' THEN 1 ELSE 0 END) sku_code_empty,
 SUM(CASE WHEN s.sku IS NOT NULL AND s.sku REGEXP '^[0-9]+$' THEN 1 ELSE 0 END) sku_numeric,
 SUM(CASE WHEN s.sku IS NOT NULL AND s.sku REGEXP '^[0-9]+$'=0 THEN 1 ELSE 0 END) sku_other
 FROM shop_yml_products yp LEFT JOIN shop_product_skus s ON s.product_id=yp.shop_id WHERE yp.profile_id=289");
 emitted('SKU289_CODE_COUNTS',$summary);
 $sample=$db->query("SELECT yp.shop_id,yp.yml_id,s.id as sku_id,s.sku,p.name FROM shop_yml_products yp JOIN shop_product p ON p.id=yp.shop_id LEFT JOIN shop_product_skus s ON s.product_id=p.id WHERE yp.profile_id=289 ORDER BY yp.shop_id DESC LIMIT 10")->fetchAll();
 $safe=array();
 foreach($sample as $r)$safe[]=array('product_id'=>(int)$r['shop_id'],'sku_id'=>isset($r['sku_id'])?(int)$r['sku_id']:null,
  'sku_missing'=>!isset($r['sku'])||trim((string)$r['sku'])==='','sku_digits_only'=>isset($r['sku'])?preg_match('/^[0-9]+$/',(string)$r['sku'])===1:null,
  'sku_length'=>isset($r['sku'])?strlen((string)$r['sku']):null,
  'offer_id_length'=>strlen((string)$r['yml_id']));
 emitted('SKU289_RECENT_PRODUCTS',$safe);
}
$sessions=$db->query("SELECT id,start_datetime,end_datetime,status,exec_type,error_message FROM shop_yml_import_sessions WHERE profile_id=289 ORDER BY id DESC LIMIT 4")->fetchAll();
emitted('SKU289_IMPORT_SESSIONS',$sessions);
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
import pathlib,re,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml')
names=[]
for f in root.rglob('*.php'):
 p=str(f.relative_to(root))
 if not re.search('Sku|SKU|Product|Import|Field|Code|Option',p,re.I):continue
 try:s=f.read_text(encoding='utf-8',errors='replace')
 except:continue
 methods=re.findall(r'\bfunction\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(',s)
 selected=[m for m in methods if re.search('generate|Sku|Code|Vendor|Map|Resolve|Create|Parse|Assign',m,re.I)]
 if selected:names.append({'file':p,'methods':selected[:23]})
print('SKU289_RELEVANT_METHODS='+json.dumps(names[:60],ensure_ascii=False))
PY
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("SKU289_"):print(line,flush=True)
if 'SKU289_PROFILE_IDENTITY=' not in out:raise RuntimeError('No SKU mapping diagnostic output')
