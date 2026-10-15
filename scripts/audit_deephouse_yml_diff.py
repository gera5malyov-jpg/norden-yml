#!/usr/bin/env python3
"""Read-only complete Deephouse feed/photo mapping audit."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/deep-pics-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat > "$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$p=$db->query("SELECT name,config FROM shop_yml_profiles WHERE id=20")->fetch();
if(!$p || strtolower(trim($p['name']))!=='deephouse')throw new RuntimeException('Profile mismatch');
$c=json_decode($p['config'],true);
$src=(string)$c['url'];
$host=parse_url($src,PHP_URL_HOST);
if($host!=='deephouse.pro')throw new RuntimeException('Unexpected supplier host');
$map=array();$duplicate=0;
foreach($db->query("SELECT yp.yml_id,yp.shop_id,p.summary,p.image_id FROM shop_yml_products yp JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=20")->fetchAll() as $x){
 $id=(string)$x['yml_id'];if(isset($map[$id])){$duplicate++;continue;}$map[$id]=$x;
}
$tmp=tempnam(sys_get_temp_dir(),'deep-photo-feed');
$f=fopen($tmp,'wb');$len=0;
$ch=curl_init($src);
curl_setopt_array($ch,array(CURLOPT_FOLLOWLOCATION=>false,CURLOPT_CONNECTTIMEOUT=>12,CURLOPT_TIMEOUT=>95,CURLOPT_WRITEFUNCTION=>function($h,$bytes)use($f,&$len){$len+=strlen($bytes);if($len>18000000)return 0;return fwrite($f,$bytes);}));
$ok=curl_exec($ch);$status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);$msg=curl_error($ch);curl_close($ch);fclose($f);
if(!$ok || $status!==200){unlink($tmp);throw new RuntimeException('Feed not readable HTTP '.$status.' '.substr($msg,0,50));}
$stats=array('mapped'=>count($map),'duplicated_mapping'=>$duplicate,'bytes'=>$len,'offers_total'=>0,
 'offers_match'=>0,'with_pictures'=>0,'mapped_without_pictures'=>0,'missing_mapping'=>0,
 'existing_extimg'=>0,'new_extimg'=>0,'no_change'=>0,'needs_change'=>0,
 'source_pictures'=>0,'existing_pictures'=>0,'photo_counts_diff'=>0,'missing_in_feed'=>0);
$seen=array();$sample=array();$hosts=array();$probes=array();$schemes=array();
$reader=new XMLReader();
if(!$reader->open($tmp,null,LIBXML_NONET|LIBXML_COMPACT))throw new RuntimeException('Invalid YML');
$reader->setParserProperty(XMLReader::SUBST_ENTITIES,false);
$reader->setParserProperty(XMLReader::VALIDATE,false);
while($reader->read()){
 if($reader->nodeType!==XMLReader::ELEMENT || $reader->localName!=='offer')continue;
 $stats['offers_total']++;
 $id=(string)$reader->getAttribute('id');
 if(!isset($map[$id]))continue;
 $seen[$id]=1;$stats['offers_match']++;
 $offer=@simplexml_load_string($reader->readOuterXML(),'SimpleXMLElement',LIBXML_NONET);
 if(!$offer)continue;
 $urls=array();
 foreach($offer->picture as $img){
  $u=trim((string)$img);$pu=parse_url($u);
  if($pu && isset($pu['scheme']) && in_array(strtolower($pu['scheme']),array('https','http'),true) && isset($pu['host']) && !in_array($u,$urls,true))$urls[]=$u;
 }
 if(isset($offer->images))foreach($offer->images->image as $img){
  $u=trim((string)$img);$pu=parse_url($u);
  if($pu && isset($pu['scheme']) && in_array(strtolower($pu['scheme']),array('https','http'),true) && isset($pu['host']) && !in_array($u,$urls,true))$urls[]=$u;
 }
 if(!$urls){$stats['mapped_without_pictures']++;continue;}
 foreach($urls as $u){$sch=parse_url($u,PHP_URL_SCHEME);$schemes[$sch]=isset($schemes[$sch])?$schemes[$sch]+1:1;}
 if(count($probes)<8 && in_array($stats['offers_match'],array(1,2,3,4,10,100,500,1000),true)){$probes[]=$urls[0];}
 $stats['with_pictures']++;$stats['source_pictures']+=count($urls);
 $summary=(string)$map[$id]['summary'];$old=array();
 preg_match_all('~\\[extimg\\](.*?)\\[/extimg\\]~is',$summary,$matches);
 foreach($matches[1] as $block)foreach(preg_split('/\\r?\\n/',trim($block)) as $line){
  $u=trim($line);if(preg_match('~^https?://~i',$u) && !in_array($u,$old,true))$old[]=$u;
 }
 $stats['existing_pictures']+=count($old);
 if($old)$stats['existing_extimg']++;else $stats['new_extimg']++;
 if(count($old)!==count($urls))$stats['photo_counts_diff']++;
 if($old===$urls)$stats['no_change']++;else $stats['needs_change']++;
 $host=parse_url($urls[0],PHP_URL_HOST);
 if($host){$hosts[$host]=isset($hosts[$host])?$hosts[$host]+1:1;}
 if(count($sample)<12 && (!$old || $old!==$urls)){
  $sample[]=array('product_id'=>(int)$map[$id]['shop_id'],'old'=>count($old),'fresh'=>count($urls),'first_source_host'=>$host,'native'=>!empty($map[$id]['image_id']));
 }
}
$reader->close();unlink($tmp);
$stats['missing_in_feed']=count($map)-count($seen);
arsort($hosts);
$checks=array();
foreach($probes as $i=>$url){
 $parts=parse_url($url);if(!$parts||strtolower($parts['host'])!=='deephouse.pro')continue;
 foreach(array('http','https') as $scheme){
  $candidate=preg_replace('~^https?://~i',$scheme.'://',$url);
  $ch=curl_init($candidate);
  curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_CONNECTTIMEOUT=>5,CURLOPT_TIMEOUT=>9,CURLOPT_SSL_VERIFYPEER=>true));
  curl_exec($ch);$checks[]=array('sample'=>$i,'scheme'=>$scheme,'status'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),'error'=>(int)curl_errno($ch),'content_type'=>curl_getinfo($ch,CURLINFO_CONTENT_TYPE));curl_close($ch);
 }
}
$httpsSource=preg_replace('~^http://~','https://',$src);
$ch=curl_init($httpsSource);curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_CONNECTTIMEOUT=>8,CURLOPT_TIMEOUT=>15,CURLOPT_SSL_VERIFYPEER=>true));curl_exec($ch);
$httpsFeed=array('http'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),'error'=>(int)curl_errno($ch));curl_close($ch);
echo 'DEEPHOUSE_FEED_AUDIT='.json_encode(array('stats'=>$stats,'image_hosts'=>array_slice($hosts,0,10,true),'image_schemes'=>$schemes,'photo_checks'=>$checks,'https_feed'=>$httpsFeed,'examples'=>$sample),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("DEEPHOUSE_"):print(line,flush=True)
if "DEEPHOUSE_FEED_AUDIT=" not in out:raise RuntimeError("No Deephouse comparison")
