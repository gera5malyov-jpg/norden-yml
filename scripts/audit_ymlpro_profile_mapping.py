#!/usr/bin/env python3
"""Read-only inspect YML PRO existing profile/table metadata, not sensitive profile values."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
FILE="$(mktemp /tmp/read-yml-profiles-XXXXXX.php)"
trap 'rm -f "$FILE"' EXIT
cat >"$FILE" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$model=new waModel();
$tables=['shop_yml_profiles','shop_yml_products','shop_yml_offer_index','shop_yml_import_sessions','shop_yml_import_stats'];
$spec=[];
foreach($tables as $table){
 try {
   $cols=$model->query('SHOW COLUMNS FROM '.$table)->fetchAll();
   $total=$model->query('SELECT COUNT(*) AS n FROM '.$table)->fetchField();
   $spec[$table]=['columns'=>array_map(function($c){return $c['Field'];},$cols),'rows'=>(int)$total];
 } catch (Throwable $e) {$spec[$table]=['error'=>'table_unavailable'];}
}
echo 'YMLPROFILE_SCHEMA='.json_encode($spec,JSON_UNESCAPED_UNICODE)."\n";
try {
 $profiles=$model->query("SELECT * FROM shop_yml_profiles LIMIT 6")->fetchAll();
 $out=[];
 foreach($profiles as $profile){
   $entry=['field_types'=>[],'nested_keys'=>[],'image_opts'=>[]];
   foreach($profile as $key=>$value){
     if (preg_match('/(?:password|token|auth|secret|key|url|uri|login)/i',$key)) continue;
     $entry['field_types'][$key]=is_scalar($value)?gettype($value):'other';
     if (!is_string($value) || strlen($value)>1000000)continue;
     $data=json_decode($value,true);
     if(!is_array($data) && preg_match('/^a:[0-9]+:/',$value)){$data=@unserialize($value,['allowed_classes'=>false]);}
     if(!is_array($data))continue;
     $entry['nested_keys'][$key]=array_keys($data);
     $queue=[['',$data]];$n=0;
     while($queue && $n<1100){
       $node=array_shift($queue);
       foreach($node[1] as $k=>$v){
         $n++;
         if(preg_match('/(image|picture|photo|summary|description|callback|field|param)/i',(string)$k)){
           $entry['image_opts'][]=['path'=>substr($node[0].'/'.$k,0,120),'type'=>gettype($v),'value'=>is_bool($v)?$v:(is_int($v)?$v:null)];
         }
         if(is_array($v) && strlen($node[0])<65 && count($queue)<120){$queue[]=[$node[0].'/'.$k,$v];}
       }
     }
     $entry['image_opts']=array_slice($entry['image_opts'],0,55);
   }
   $out[]=$entry;
 }
 echo 'YMLPROFILE_METADATA='.json_encode($out,JSON_UNESCAPED_UNICODE)."\n";
}catch(Throwable $e){echo "YMLPROFILE_METADATA_ERROR=".substr(get_class($e),0,40)."\n";}
PHP
chmod 644 "$FILE"
su -s /bin/bash web -c "php -d display_errors=0 '$FILE'"
'''
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith('YMLPROFILE_'):print(line,flush=True)
if 'YMLPROFILE_SCHEMA=' not in out:raise RuntimeError('No metadata report')
