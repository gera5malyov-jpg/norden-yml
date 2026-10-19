#!/usr/bin/env python3
"""Read-only catalog feature audit. Never changes product, feature or supplier data."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
p=$(mktemp /tmp/megapolis-feature-audit-XXXXXXXX.php)
trap 'rm -f "$p"' EXIT
cat > "$p" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
ini_set('memory_limit','768M');
set_time_limit(170);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');$m=new waModel();
function out($name,$data){echo 'FEATURE_AUDIT_'.$name.'='.json_encode($data,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PARTIAL_OUTPUT_ON_ERROR)."\n";flush();}
$features=$m->query('SELECT id,code,name,type,default_unit FROM shop_feature ORDER BY id')->fetchAll();
$types=array();$odd=array();$dupes=array();$byName=array();$bools=array();
foreach($features as $f){
 $type=$f['type'];$types[$type]=isset($types[$type])?$types[$type]+1:1;
 $name=trim((string)$f['name']);$key=mb_strtolower(preg_replace('/[\s\x{00A0}]+/u','',$name),'UTF-8');
 if(!isset($byName[$key]))$byName[$key]=array();$byName[$key][]=$f['id'];
 $reason=array();
 if(preg_match('/(?:true|false|bool|boolean|^[-\s]*$)/iu',$name))$reason[]='boolean_marker';
 if(preg_match('/[a-zа-яё][A-ZА-ЯЁ]/u',$name))$reason[]='joined_words';
 if(preg_match('/[0-9]{2,}|[_]{2,}|[<>]|https?:|[()]{2,}/iu',$name))$reason[]='noise';
 if(preg_match('/(мм|см|м2|кг|гр|л)$/iu',$name) && strpos((string)$f['type'],'dimension')===false)$reason[]='unit_in_name';
 if($reason)$odd[]=array('id'=>$f['id'],'code'=>$f['code'],'name'=>$name,'type'=>$type,'reasons'=>$reason);
 if(strpos($type,'boolean')!==false)$bools[]=array('id'=>$f['id'],'code'=>$f['code'],'name'=>$name,'type'=>$type);
}
foreach($byName as $name=>$ids)if(count($ids)>1)$dupes[]=array('normalized_name'=>$name,'ids'=>$ids);
out('DEFINITIONS',array('feature_count'=>count($features),'types'=>$types,'questionable_names'=>count($odd),'duplicate_name_groups'=>count($dupes),'boolean_definitions'=>count($bools)));
out('NAMES',array_slice($odd,0,180));
out('DUPLICATES',array_slice($dupes,0,120));
out('BOOL_FEATURES',array_slice($bools,0,100));
$counts=$m->query("SELECT (SELECT COUNT(*) FROM shop_product) products_total,(SELECT COUNT(*) FROM shop_product WHERE category_id IS NOT NULL AND category_id>0) categorized_products, (SELECT COUNT(*) FROM shop_product_features) feature_links")->fetchAssoc();out('COUNTS',$counts);
$top=$m->query('SELECT pf.feature_id,COUNT(*) links,COUNT(DISTINCT pf.product_id) products FROM shop_product_features pf GROUP BY pf.feature_id ORDER BY links DESC LIMIT 90')->fetchAll();
$byId=array();foreach($features as $f)$byId[$f['id']]=$f;
foreach($top as &$r){$f=isset($byId[$r['feature_id']])?$byId[$r['feature_id']]:array();$r['code']=isset($f['code'])?$f['code']:null;$r['name']=isset($f['name'])?$f['name']:null;$r['type']=isset($f['type'])?$f['type']:null;}
out('TOP_FEATURES',$top);
$tables=$m->query("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name LIKE 'shop_feature_values_%' ORDER BY table_name")->fetchAll();
foreach($tables as $t){$table=$t['table_name'];if(!preg_match('/^shop_feature_values_[a-z]+$/',$table))continue;
 $columns=$m->query("SHOW COLUMNS FROM ".$table)->fetchAll();
 out('VALUE_TABLE',array('table'=>$table,'columns'=>array_map(function($a){return $a['Field'];},$columns)));
}
$urls=array('stul-kenner-313sd-chernyy-zhemchug-e10-334-322382','stol-obedennyy-02-02a-razbornyy-karkas-konyak');
foreach($urls as $url){
 $p=$m->query("SELECT id,name,type_id,category_id FROM shop_product WHERE url=s:u LIMIT 1",array('u'=>$url))->fetchAssoc();
 if(!$p){out('SAMPLE',array('url'=>$url,'missing'=>true));continue;}
 $prod=new shopProduct($p['id']);$s=array();
 foreach($prod->features as $code=>$v){$value=is_array($v)?implode(' | ',array_map('strval',$v)):(string)$v;$s[]=array('code'=>$code,'name'=>isset($prod->features[$code])?$code:$code,'display'=>mb_substr($value,0,180,'UTF-8'));}
 out('SAMPLE',array('product'=>$p,'feature_count'=>count($s),'features'=>array_slice($s,0,80)));
}
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php -d display_errors=0 -d log_errors=1 '$p'"
'''
if __name__=='__main__':
 r=execute(REMOTE)
 seen=[]
 for line in r.splitlines():
  if line.startswith('FEATURE_AUDIT_'):
   print(line,flush=True)
   seen.append(line.split('=',1)[0])
 if 'FEATURE_AUDIT_DEFINITIONS' not in seen:raise RuntimeError('Audit not completed')
