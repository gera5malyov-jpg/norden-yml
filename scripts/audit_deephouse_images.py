#!/usr/bin/env python3
"""Read-only Deephouse YML PRO profile audit. No product modification."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/deephouse-audit-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat > "$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$p=$db->query("SELECT id,name,config FROM shop_yml_profiles WHERE id=20")->fetch();
if(!$p)throw new RuntimeException("Deephouse profile missing");
$c=json_decode($p['config'],true);
if(!is_array($c))throw new RuntimeException("Bad profile settings");
$u=(string)(isset($c['url'])?$c['url']:'');
$parts=parse_url($u);
$agg=$db->query("SELECT COUNT(*) AS mappings, COUNT(DISTINCT yp.shop_id) AS products,
 SUM(p.id IS NULL) AS missing, SUM(p.summary LIKE '%[extimg]%') AS extimg,
 SUM(p.image_id>0) AS native, SUM(p.status=1) AS published,
 SUM(p.summary IS NULL OR p.summary='') AS empty_summary
 FROM shop_yml_products yp LEFT JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=20")->fetch();
$shared=$db->query("SELECT COUNT(DISTINCT yp.shop_id) FROM shop_yml_products yp JOIN shop_yml_products others
 ON yp.shop_id=others.shop_id AND others.profile_id<>20 WHERE yp.profile_id=20")->fetchField();
$examples=$db->query("SELECT yp.shop_id,p.url,p.summary,p.name,p.image_id,yp.yml_id
 FROM shop_yml_products yp JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=20 ORDER BY yp.shop_id LIMIT 8")->fetchAll();
$sample=array();
foreach($examples as $row){
 $summary=(string)$row['summary'];
 preg_match_all('~\\[extimg\\](.*?)\\[/extimg\\]~is',$summary,$matches);
 $picHost=null;
 if(count($matches[1])){foreach(explode("\n",trim($matches[1][0])) as $one){$picHost=parse_url(trim($one),PHP_URL_HOST);if($picHost)break;}}
 $sample[]=array('id'=>(int)$row['shop_id'],'photo_blocks'=>count($matches[1]),'photo_host'=>$picHost,'native'=>(int)$row['image_id']>0,'url'=>$row['url'],'offer_len'=>strlen((string)$row['yml_id']));
}
$info=array('name'=>$p['name'],'profile_id'=>20,'source_scheme'=>isset($parts['scheme'])?$parts['scheme']:null,
 'source_host'=>isset($parts['host'])?$parts['host']:null, 'path_suffix'=>substr(isset($parts['path'])?$parts['path']:'',-8),
 'source_type'=>isset($c['source_type'])?$c['source_type']:null,'images_mode'=>isset($c['images'])?$c['images']:null,
 'max_img'=>isset($c['max_img'])?$c['max_img']:null,'has_auth'=>!empty($c['http_login'])||!empty($c['http_auth']),
 'url_bytes'=>strlen($u),'server_file_set'=>!empty($c['server_file']),
 'server_file_basename'=>!empty($c['server_file'])?basename((string)$c['server_file']):null,
 'local_file_set'=>!empty($c['local_file']),
 'local_file_basename'=>!empty($c['local_file'])?basename((string)$c['local_file']):null,
 'config_hash'=>substr(hash('sha256',(string)$p['config']),0,16),
 'stats'=>$agg,'shared_products'=>(int)$shared,'examples'=>$sample);
echo 'DEEPHOUSE_PROFILE='.json_encode($info,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if($u){
 $ch=curl_init($u);
 curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>true,CURLOPT_MAXREDIRS=>4,CURLOPT_CONNECTTIMEOUT=>10,CURLOPT_TIMEOUT=>22,CURLOPT_SSL_VERIFYPEER=>true));
 curl_exec($ch);
 echo 'DEEPHOUSE_SOURCE_HEAD='.json_encode(array('http'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),
 'effective_scheme'=>parse_url(curl_getinfo($ch,CURLINFO_EFFECTIVE_URL),PHP_URL_SCHEME),
 'effective_host'=>parse_url(curl_getinfo($ch,CURLINFO_EFFECTIVE_URL),PHP_URL_HOST),
 'size'=>(int)curl_getinfo($ch,CURLINFO_CONTENT_LENGTH_DOWNLOAD),'error_kind'=>curl_errno($ch)),JSON_UNESCAPED_UNICODE)."\n";
 curl_close($ch);
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("DEEPHOUSE_"):print(line,flush=True)
if "DEEPHOUSE_PROFILE=" not in out:raise RuntimeError("Missing Deephouse audit")
