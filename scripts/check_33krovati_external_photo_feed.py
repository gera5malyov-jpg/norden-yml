#!/usr/bin/env python3
"""Read-only compare 33krovati/4 Seasons image feed offer IDs to YML PRO mappings."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
F="$(mktemp /tmp/check-33k-photofeed-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat > "$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$maps=array();
foreach($db->query("SELECT m.yml_id,m.shop_id,p.summary,s.sku,s.name sku_name FROM shop_yml_products m
 JOIN shop_product p ON p.id=m.shop_id
 JOIN shop_product_skus s ON s.product_id=p.id
 WHERE m.profile_id=289")->fetchAll() as $r){
 $maps[(string)$r['yml_id']]=$r;
}
$recent=$db->query("SELECT m.shop_id,m.yml_id,s.sku,s.name sku_name,p.summary FROM shop_yml_products m
 JOIN shop_product p ON p.id=m.shop_id JOIN shop_product_skus s ON s.product_id=p.id
 WHERE m.profile_id=289 ORDER BY m.shop_id DESC LIMIT 10")->fetchAll();
$s=array();
foreach($recent as $x) {
 $s[]=array('product'=>(int)$x['shop_id'],'code'=>mb_substr((string)$x['sku'],0,55),
 'sku_name_blank'=>trim((string)$x['sku_name'])==='',
 'feed_key'=>mb_substr((string)$x['yml_id'],0,55),
 'has_images'=>strpos((string)$x['summary'],'[extimg]')!==false);
}
echo 'FOURSEASONS_RECENT_SKU='.json_encode($s,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES).PHP_EOL;
$url='https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/4s-mebel.yml';
$tmp=tempnam(sys_get_temp_dir(),'yml33k-');
$fh=fopen($tmp,'wb');$bytes=0;
$ch=curl_init($url);
curl_setopt_array($ch,array(CURLOPT_TIMEOUT=>80,CURLOPT_CONNECTTIMEOUT=>12,CURLOPT_FOLLOWLOCATION=>false,
 CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_WRITEFUNCTION=>function($ch,$part) use($fh,&$bytes){
 $bytes+=strlen($part); if($bytes>14000000)return 0;return fwrite($fh,$part);
}));
$ok=curl_exec($ch);$status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);curl_close($ch);fclose($fh);
if(!$ok||$status!==200)throw new RuntimeException('Cannot access published 4-Seasons feed');
$reader=new XMLReader();
$reader->open($tmp,null,LIBXML_NONET|LIBXML_COMPACT);
$reader->setParserProperty(XMLReader::SUBST_ENTITIES,false);
$matched=0;$offers=0;$matched_no_images=0;$matched_with_images=0;$unmatched=0;$pictures=0;$mapIds=array();$missingExamples=array();
while($reader->read()){
 if($reader->nodeType!==XMLReader::ELEMENT||$reader->localName!=='offer')continue;
 $offers++;$id=(string)$reader->getAttribute('id');
 if(!isset($maps[$id])){$unmatched++;continue;}
 $mapIds[$id]=true;$matched++;
 $hasImage=strpos((string)$maps[$id]['summary'],'[extimg]')!==false;
 if($hasImage)$matched_with_images++;else{$matched_no_images++;if(count($missingExamples)<9)$missingExamples[]=array('product'=>(int)$maps[$id]['shop_id'],'key'=>substr($id,0,50));}
 $outer=$reader->readOuterXML();$pictures+=substr_count($outer,'<picture>');
}
$reader->close();@unlink($tmp);
echo 'FOURSEASONS_FEED='.json_encode(array('source'=>'GitHub 4s-mebel.yml','http'=>$status,'bytes'=>$bytes,
 'offers'=>$offers,'mapped_matches'=>$matched,'unmapped_offers'=>$unmatched,'matched_with_extimg'=>$matched_with_images,
 'matched_without_extimg'=>$matched_no_images,'pictures_in_matched'=>$pictures,
 'mapped_not_in_feed'=>count($maps)-$matched,'new_missing_image_examples'=>$missingExamples),JSON_UNESCAPED_UNICODE).PHP_EOL;
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=1 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith('FOURSEASONS_'):print(line,flush=True)
if 'FOURSEASONS_FEED=' not in out:raise RuntimeError('No 33krovati photo feed result')
