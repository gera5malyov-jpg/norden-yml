#!/usr/bin/env python3
"""Read only verify 3 updated product descriptions and image URL reachability."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/etz-verify-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$m=new shopProductModel();
foreach(array(1473362,1473363,1473364) as $id) {
 $p=$m->getById($id);
 if(!$p){echo 'ETZ_VERIFY='.json_encode(array('id'=>$id,'missing'=>true))."\n";continue;}
 $summary=(string)$p['summary'];
 preg_match('~\[extimg\]\s*(https?://[^\s<\[]+)~i',$summary,$match);
 $src=isset($match[1])?html_entity_decode($match[1],ENT_QUOTES,'UTF-8'):'';
 $checks=array();
 if($src){
  foreach(array('original'=>$src,'try_https'=>preg_replace('~^http://~i','https://',$src)) as $kind=>$url) {
   $ch=curl_init($url);
   curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,
    CURLOPT_FOLLOWLOCATION=>false,CURLOPT_TIMEOUT=>7,CURLOPT_CONNECTTIMEOUT=>4,
    CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2));
   curl_exec($ch);
   $checks[$kind]=array('status'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),
       'type'=>(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE));
   curl_close($ch);
  }
 }
 echo 'ETZ_VERIFY='.json_encode(array('id'=>$id,'url'=>(string)$p['url'],
  'has_extimg'=>!!$src,'image_protocol'=>$src?parse_url($src,PHP_URL_SCHEME):null,
  'image_host'=>$src?parse_url($src,PHP_URL_HOST):null,'checks'=>$checks),
  JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith('ETZ_VERIFY='):print(line,flush=True)
