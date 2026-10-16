#!/usr/bin/env python3
"""Read-only, secret-safe diagnostics for YML PRO profile 4sis only."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/4sis-photo-diagnose-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$r=$db->query("SELECT id,name,config FROM shop_yml_profiles WHERE id=19")->fetch();
if(!$r || trim((string)$r['name'])!=='4sis')throw new RuntimeException('4sis profile mismatch');
$c=json_decode($r['config'],true);if(!is_array($c))throw new RuntimeException('Invalid configuration');
$url=isset($c['url'])?trim((string)$c['url']):'';
$parts=$url?parse_url($url):array();
$out=array('id'=>19,'profile'=>'4sis',
'source_type'=>isset($c['source_type'])?$c['source_type']:null,
'scheme'=>isset($parts['scheme'])?$parts['scheme']:'',
'host'=>isset($parts['host'])?$parts['host']:'',
'has_url'=>$url!=='',
'url_valid'=>is_array($parts),
'embedded_user'=>isset($parts['user']),
'embedded_password'=>isset($parts['pass']),
'has_custom_http_login'=>!empty($c['http_login']),
'has_custom_http_auth'=>!empty($c['http_auth']),
'image_mode'=>isset($c['images'])?$c['images']:null,
'url_has_query'=>!empty($parts['query']),
'url_has_fragment'=>!empty($parts['fragment']));
foreach(array('local_file','server_file','bind_to_profile','raw_image_links','image_hooks') as $k){
 $v=array_key_exists($k,$c)?$c[$k]:null;
 $info=array('type'=>gettype($v),'set'=>($v!==null&&$v!==false&&$v!==''&&$v!==array()));
 if(is_string($v)&&$v!==''){
  $info['extension']=strtolower(pathinfo(parse_url($v,PHP_URL_PATH)?:$v,PATHINFO_EXTENSION));
  $info['is_http']=strpos($v,'http://')===0||strpos($v,'https://')===0;
  $info['is_regular_file']=is_file($v);
  $info['basename_is_hidden']=substr(basename($v),0,1)==='.';
 }
 if(is_bool($v)||is_int($v))$info['value']=$v;
 $out['source_setting_'.$k]=$info;
}
$sessions=$db->query("SELECT id, status, start_datetime, end_datetime FROM shop_yml_import_sessions WHERE profile_id=19 ORDER BY id DESC LIMIT 3")->fetchAll();
$out['recent_imports']=$sessions;
$hoststat=array();
$pics=$db->query("SELECT p.summary FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id=19")->fetchAll();
foreach($pics as $p){if(preg_match('~\\[extimg\\]\\s*(https?://[^\\s]+)~',(string)$p['summary'],$m)){ $h=parse_url($m[1],PHP_URL_HOST); if($h)$hoststat[$h]=isset($hoststat[$h])?$hoststat[$h]+1:1; }}
arsort($hoststat);
$out['existing_photo_hosts']=array_slice($hoststat,0,5,true);
$candidate=isset($c['bind_to_profile'])?$c['bind_to_profile']:'';
if(is_string($candidate) && is_file($candidate)){
 $out['bound_source_file']=array('size_bytes'=>filesize($candidate),'age_hours'=>round((time()-filemtime($candidate))/3600,1),
   'head_type'=>'unknown','root_element'=>'','offer_count_sample'=>0,'picture_count_sample'=>0,'image_hosts_sample'=>array());
 $b=fopen($candidate,'rb');$h=fread($b,512);fclose($b);
 $out['bound_source_file']['head_type']=(strpos(ltrim($h),'<?xml')===0 || strpos(ltrim($h),'<yml_catalog')===0)?'xml':'other';
 if($out['bound_source_file']['head_type']==='xml'){
  $reader=new XMLReader();
  if($reader->open($candidate,null,LIBXML_NONET|LIBXML_COMPACT)){
   $reader->setParserProperty(XMLReader::SUBST_ENTITIES,false);
   while($reader->read()){
    if($reader->nodeType!==XMLReader::ELEMENT)continue;
    if($out['bound_source_file']['root_element']==='')$out['bound_source_file']['root_element']=$reader->localName;
    if($reader->localName!=='offer')continue;
    $out['bound_source_file']['offer_count_sample']++;
    $xml=$reader->readOuterXML();
    preg_match_all('~<picture>([^<]+)</picture>~',$xml,$mm);
    $out['bound_source_file']['picture_count_sample']+=count($mm[1]);
    foreach($mm[1] as $url){$host=parse_url(html_entity_decode($url),PHP_URL_HOST);if($host)$out['bound_source_file']['image_hosts_sample'][$host]=1;}
    if($out['bound_source_file']['offer_count_sample']>=5)break;
   }$reader->close();
  }
 }
}
$out['yml_tables']=array();
foreach($db->query("SHOW TABLES LIKE 'shop_yml_%'")->fetchAll() as $r){
 $name=current($r);if(is_string($name))$out['yml_tables'][]=$name;
}
$out['import_sessions_columns']=array_map(function($r){return $r['Field'];},$db->query("SHOW COLUMNS FROM shop_yml_import_sessions")->fetchAll());
$out['yml_profiles_columns']=array_map(function($r){return $r['Field'];},$db->query("SHOW COLUMNS FROM shop_yml_profiles")->fetchAll());
foreach(array('shop_yml_offer_index','shop_yml_finder_cache','shop_yml_import_stats','shop_yml_products') as $table){
 $cols=array_map(function($r){return $r['Field'];},$db->query("SHOW COLUMNS FROM ".$table)->fetchAll());
 $info=array('columns'=>$cols);
 if(in_array('profile_id',$cols,true)){
  $r=$db->query("SELECT COUNT(*) n FROM ".$table." WHERE profile_id=19")->fetch();
  $info['profile_19_rows']=(int)$r['n'];
 }
 $out['table_'.$table]=$info;
}
$fields=array_keys($c);$out['image_keys']=array_values(array_filter($fields,function($k){return preg_match('/image|photo|picture|source|file|url/i',$k);}));
echo 'FOURSIS_SOURCE='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for x in out.splitlines():
 if x.startswith("FOURSIS_SOURCE="): print(x,flush=True)
if "FOURSIS_SOURCE=" not in out: raise RuntimeError("Diagnostic result missing")
