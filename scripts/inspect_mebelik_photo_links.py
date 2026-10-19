#!/usr/bin/env python3
"""Read-only diagnose Mebelik feed reachability, cache and 3 existing product photos."""
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
$info=shopYmlFilesHelper::getFileInfo(28);
$url=isset($info['url'])?(string)$info['url']:'';
$p=parse_url($url);
echo 'MBK_SOURCE='.json_encode(array('type'=>isset($info['upload_type'])?$info['upload_type']:'unset','scheme'=>isset($p['scheme'])?$p['scheme']:'','host'=>isset($p['host'])?$p['host']:'','path'=>isset($p['path'])?$p['path']:''),JSON_UNESCAPED_SLASHES)."\n";
$prof=new shopYmlProfile(28);
$local=$prof->getFile()->getFile(true);
echo 'MBK_CACHE='.json_encode(array('exists'=>($local && is_file($local)),'size'=>($local && is_file($local))?filesize($local):null,
'age_hours'=>($local && is_file($local))?round((time()-filemtime($local))/3600,2):null))."\n";
function testLink($url) {
 $ch=curl_init($url);
 curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,
  CURLOPT_FOLLOWLOCATION=>false,CURLOPT_TIMEOUT=>12,CURLOPT_CONNECTTIMEOUT=>6,
  CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_USERAGENT=>'Mozilla/5.0'));
 curl_exec($ch);
 $ret=array('code'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),
   'mime'=>(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE),'errno'=>curl_errno($ch),
   'err'=>substr(curl_error($ch),0,130));
 curl_close($ch);
 return $ret;
}
if($url){
 echo 'MBK_FEED='.json_encode(array('https_head'=>testLink($url)),JSON_UNESCAPED_UNICODE)."\n";
 if(stripos($url,'https://')===0)echo 'MBK_FEED_HTTP='.json_encode(array('http_head'=>testLink('http://'.substr($url,8)),'no_www_https'=>testLink(str_replace('://www.mebelik.ru/', '://mebelik.ru/', $url))),JSON_UNESCAPED_UNICODE)."\n";
}
$rows=$db->query('SELECT m.shop_id,p.url,p.summary FROM shop_yml_products m JOIN shop_product p ON m.shop_id=p.id WHERE m.profile_id=28 LIMIT 3')->fetchAll();
foreach($rows as $row){
 preg_match('~\[extimg\]\s*(https?://[^\s<\[]+)~i',(string)$row['summary'],$m);
 if(empty($m[1]))continue;
 $src=html_entity_decode($m[1],ENT_QUOTES,'UTF-8');
 $parts=parse_url($src);
 $https=str_replace('http://','https://',$src);
 $canonical=preg_replace('~^https?://www[.]mebelik[.]ru~i','https://mebelik.ru',$src);
 echo 'MBK_IMAGE='.json_encode(array('id'=>(int)$row['shop_id'],'product_url'=>$row['url'],'scheme'=>isset($parts['scheme'])?$parts['scheme']:'',
 'path'=>isset($parts['path'])?substr($parts['path'],0,220):'','canonical_host'=>parse_url($canonical, PHP_URL_HOST),'http'=>testLink($src),'https'=>testLink($https),'canonical'=>testLink($canonical)),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
$idx=$db->query('SELECT picture_url FROM shop_yml_offer_index WHERE profile_id=28 AND picture_url IS NOT NULL AND picture_url!="" LIMIT 4')->fetchAll();
foreach($idx as $row){$p=parse_url($row['picture_url']);echo 'MBK_OFFER_IMG='.json_encode(array('scheme'=>isset($p['scheme'])?$p['scheme']:'','host'=>isset($p['host'])?$p['host']:'','path'=>isset($p['path'])?substr($p['path'],0,160):''),JSON_UNESCAPED_SLASHES)."\n";}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
python3 - <<'PY'
from pathlib import Path
p=Path('wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php')
lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
for a,b in ((60,130),(130,170),(215,239)):
 for i in range(a-1,min(len(lines),b)):
  print('MBK_CODE='+str(i+1)+':'+lines[i][:280])
PY
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith(("MBK_SOURCE=","MBK_CACHE=","MBK_FEED=","MBK_FEED_HTTP=","MBK_IMAGE=","MBK_OFFER_IMG=","MBK_CODE=")):print(line,flush=True)
