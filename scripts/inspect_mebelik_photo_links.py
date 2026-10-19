#!/usr/bin/env python3
"""Read-only Mebelik discovery: YML profiles, type links, source metadata and sample image status."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mebelik-inspect-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$rows=$db->query('SELECT id,name,config FROM shop_yml_profiles ORDER BY id')->fetchAll();
$target=array();
foreach($rows as $r) {
 if(stripos($r['name'],'mebelik')!==false || mb_stripos($r['name'],'мебелик')!==false) {
  $target[]=(int)$r['id'];
  $info=shopYmlFilesHelper::getFileInfo((int)$r['id']);
  $cfg=json_decode($r['config'],true);
  $source=(string)(isset($info['url'])?$info['url']:(isset($cfg['url'])?$cfg['url']:''));
  $p=$source?parse_url($source):array();
  $count=(int)$db->query('SELECT COUNT(*) FROM shop_yml_products WHERE profile_id='.(int)$r['id'])->fetchField();
  $stats=array('id'=>(int)$r['id'],'name'=>$r['name'],'mapped'=>$count,
   'profile_old_url_nonempty'=>!empty($cfg['url']),
   'file_manager_source'=>isset($info['upload_type'])?$info['upload_type']:'none',
   'source_scheme'=>isset($p['scheme'])?$p['scheme']:null,
   'source_host'=>isset($p['host'])?$p['host']:null,
   'source_path'=>isset($p['path'])?$p['path']:null);
  echo 'MBK_PROFILE='.json_encode($stats,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
  $pid=(int)$r['id'];
  $samples=$db->query('SELECT m.yml_id,m.shop_id,p.name,p.url,p.summary FROM shop_yml_products m LEFT JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id='.$pid.' LIMIT 5')->fetchAll();
  foreach($samples as $s){
   $summary=(string)$s['summary'];
   preg_match_all('~\[extimg\](.*?)\[/extimg\]~isu',$summary,$matches);
   $first=count($matches[1])?trim($matches[1][0]):'';
   $pp=$first?parse_url($first):array();
   echo 'MBK_SAMPLE='.json_encode(array('id'=>(int)$s['shop_id'],'offer'=>(string)$s['yml_id'],
    'name'=>$s['name'],'url'=>$s['url'],'extimgs'=>count($matches[1]),
    'summary_length'=>strlen($summary),'first_scheme'=>isset($pp['scheme'])?$pp['scheme']:null,
    'first_host'=>isset($pp['host'])?$pp['host']:null),
    JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
  }
 }
}
if(!$target)echo "MBK_PROFILE=NONE\n";
$types=$db->query('SELECT id,name FROM shop_type ORDER BY id')->fetchAll();
foreach($types as $r) {
 if(stripos($r['name'],'mebelik')!==false || mb_stripos($r['name'],'мебелик')!==false){
   $tid=(int)$r['id'];
   $qty=(int)$db->query('SELECT COUNT(*) FROM shop_product WHERE type_id='.$tid)->fetchField();
   $cnt=(int)$db->query("SELECT COUNT(*) FROM shop_product WHERE type_id=".$tid." AND summary LIKE '%[extimg]%'")->fetchField();
   echo 'MBK_TYPE='.json_encode(array('id'=>$tid,'name'=>$r['name'],'products'=>$qty,'with_extimg'=>$cnt),JSON_UNESCAPED_UNICODE)."\n";
 }
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith(("MBK_PROFILE=","MBK_SAMPLE=","MBK_TYPE=")):print(line,flush=True)
