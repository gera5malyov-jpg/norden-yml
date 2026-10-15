#!/usr/bin/env python3
"""Read-only test whether feed <offer id> matches YML PRO's saved mapping."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'storefront'))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
R=/home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml-photo-source-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
$root='/home/web/vm-23f9aff9.na4u.ru/www';chdir($root);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
foreach(array(231,31) as $pid) {
 $prof=$db->query("SELECT config FROM shop_yml_profiles WHERE id=".(int)$pid)->fetch();
 if(!$prof)continue;
 $cfg=json_decode($prof['config'],true);$url=isset($cfg['url'])?$cfg['url']:'';
 $info=array('id'=>$pid,'source_host'=>parse_url($url,PHP_URL_HOST),'mapped'=>0,'offer_total'=>0,'matches'=>array(),'samples'=>array());
 $tmp=tempnam(sys_get_temp_dir(),'yml-photo-audit-');
 try{
  if(strpos($url,'https://')!==0)throw new Exception('Source not HTTPS');
  $fd=fopen($tmp,'wb');$size=0;
  $curl=curl_init($url);
  curl_setopt_array($curl,array(CURLOPT_FOLLOWLOCATION=>true,CURLOPT_MAXREDIRS=>3,CURLOPT_TIMEOUT=>110,CURLOPT_CONNECTTIMEOUT=>12,CURLOPT_SSL_VERIFYPEER=>true,
   CURLOPT_WRITEFUNCTION=>function($ch,$chunk) use ($fd,&$size){$size+=strlen($chunk);if($size>85000000)return 0;return fwrite($fd,$chunk);}));
  $ok=curl_exec($curl);$http=curl_getinfo($curl,CURLINFO_HTTP_CODE);$msg=curl_error($curl);curl_close($curl);fclose($fd);
  if(!$ok||$http!=200)throw new Exception('Feed unavailable HTTP '.(int)$http.' '.substr($msg,0,50));
  $info['bytes']=$size;
  $map=array();$rows=$db->query("SELECT yml_id,shop_id FROM shop_yml_products WHERE profile_id=".(int)$pid)->fetchAll();
  foreach($rows as $r){$map[(string)$r['yml_id']][]=(int)$r['shop_id'];}
  $info['mapped']=count($map);
  $r=new XMLReader();if(!$r->open($tmp,null,LIBXML_NONET|LIBXML_COMPACT))throw new Exception('XMLReader failed');
  while($r->read()){
   if($r->nodeType===XMLReader::DOC_TYPE)throw new Exception('Unsafe DTD');
   if($r->nodeType!==XMLReader::ELEMENT||$r->localName!=='offer')continue;
   $id=$r->getAttribute('id');$info['offer_total']++;
   if($id===null||!isset($map[$id]))continue;
   $x=$r->readOuterXML();$item=@simplexml_load_string($x,'SimpleXMLElement',LIBXML_NONET);
   if(!$item)continue;
   $pictures=array();
   foreach($item->picture as $p){$u=trim((string)$p);if(strpos($u,'https://')===0 || strpos($u,'http://')===0){$pictures[]=$u;}}
   if(!$pictures)continue;
   $ids=array_unique($map[$id]);
   $info['matches'][]=array('offer_hash'=>substr(hash('sha256',$id),0,12),'shop_id'=>count($ids)===1?$ids[0]:null,
      'pictures'=>count($pictures),'first_host'=>parse_url($pictures[0],PHP_URL_HOST),'ambiguous'=>count($ids)!==1);
   if(count($info['matches'])>=9)break;
  }
  $r->close();
 }catch(Throwable $e){$info['error']=substr($e->getMessage(),0,160);}
 finally{if(file_exists($tmp))unlink($tmp);}
 echo 'PILOT_FEED='.json_encode($info,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
r=execute(REMOTE)
for l in r.splitlines():
 if l.startswith('PILOT_FEED='):print(l,flush=True)
if 'PILOT_FEED=' not in r:raise RuntimeError('Missing offer mapping evidence')
