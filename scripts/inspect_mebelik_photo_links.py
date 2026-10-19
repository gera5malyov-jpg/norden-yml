#!/usr/bin/env python3
"""Read-only check Mebelik catalog cards and live storefront rendering."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mbk-public-check-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
foreach(array(742926,742971,742927,742751) as $id){
 $p=$db->query('SELECT id,name,url,summary FROM shop_product WHERE id='.(int)$id)->fetch();
 if(!$p){echo "MBK_VERIFY=missing:".$id."\n";continue;}
 preg_match_all('~\[extimg\](.*?)\[/extimg\]~isu',(string)$p['summary'],$matches);
 $first=isset($matches[1][0])?trim(html_entity_decode($matches[1][0],ENT_QUOTES,'UTF-8')):'';
 $imgtest=array('code'=>0,'mime'=>'');
 if($first){
  $ch=curl_init($first);
  curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,
   CURLOPT_CONNECTTIMEOUT=>6,CURLOPT_TIMEOUT=>14,CURLOPT_FOLLOWLOCATION=>false,
   CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,
   CURLOPT_CAINFO=>'/home/web/vm-23f9aff9.na4u.ru/www/wa-config/mebelik-globalsign-root-r46.pem'));
  curl_exec($ch);
  $imgtest=array('code'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),'mime'=>(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE),'errno'=>curl_errno($ch));
  curl_close($ch);
 }
 $page='https://profikompany.ru/'.$p['url'].'/';
 $ch=curl_init($page);
 curl_setopt_array($ch,array(CURLOPT_RETURNTRANSFER=>true,CURLOPT_TIMEOUT=>22,CURLOPT_CONNECTTIMEOUT=>8,
  CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_FOLLOWLOCATION=>true));
 $body=curl_exec($ch);
 $pagecode=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
 $pageerr=curl_errno($ch);
 curl_close($ch);
 $result=array('id'=>(int)$p['id'],'slug'=>$p['url'],'name'=>$p['name'],
  'extimg_count'=>count($matches[1]),'image_scheme'=>$first?parse_url($first,PHP_URL_SCHEME):null,
  'image_host'=>$first?parse_url($first,PHP_URL_HOST):null,
  'image_check'=>$imgtest,'page_code'=>$pagecode,'page_errno'=>$pageerr,
  'page_contains_mebelik'=>is_string($body)&&stripos($body,'mebelik.ru')!==false,
  'page_contains_https_mebelik'=>is_string($body)&&stripos($body,'https://mebelik.ru')!==false);
 echo 'MBK_VERIFY='.json_encode($result,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith("MBK_VERIFY="):print(line,flush=True)
