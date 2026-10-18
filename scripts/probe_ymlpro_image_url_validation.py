#!/usr/bin/env python3
"""Read-only check: compare YML Pro photo HEAD to browser-like GET responses."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/yml-image-probe-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
require_once 'wa-apps/shop/plugins/univimport/lib/classes/shopUnivimportPluginUniversalPhotoSync.class.php';
$db=new waModel();
$cls=new ReflectionClass('shopUnivimportPluginUniversalPhotoSync');
$methodProfile=$cls->getMethod('profile');$methodProfile->setAccessible(true);
$methodPlan=$cls->getMethod('plan');$methodPlan->setAccessible(true);
function test_link($url,$verb,$follow) {
 $ch=curl_init($url);$received=0;
 $opts=array(CURLOPT_CONNECTTIMEOUT=>7,CURLOPT_TIMEOUT=>20,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,
 CURLOPT_FOLLOWLOCATION=>$follow,CURLOPT_MAXREDIRS=>3,CURLOPT_NOBODY=>$verb==='head',
 CURLOPT_RETURNTRANSFER=>false,CURLOPT_USERAGENT=>'Mozilla/5.0 (compatible; catalog-photo-check/1.0)',
 CURLOPT_WRITEFUNCTION=>function($ch,$bytes)use(&$received){$received+=strlen($bytes);return $received>350000?0:strlen($bytes);});
 if($verb==='get')$opts[CURLOPT_RANGE]='0-16383';
 if(defined('CURLOPT_PROTOCOLS'))$opts[CURLOPT_PROTOCOLS]=CURLPROTO_HTTPS;
 if(defined('CURLOPT_REDIR_PROTOCOLS'))$opts[CURLOPT_REDIR_PROTOCOLS]=CURLPROTO_HTTPS;
 curl_setopt_array($ch,$opts);
 curl_exec($ch);
 $status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
 $type=(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE);
 $redir=(int)curl_getinfo($ch,CURLINFO_REDIRECT_COUNT);
 $err=curl_errno($ch);curl_close($ch);
 return array('status'=>$status,'type'=>substr($type,0,80),'bytes'=>$received,'redirects'=>$redir,'curl_errno'=>$err);
}
foreach(array(91,202,311) as $id){
 try{
   $p=$methodProfile->invoke(null,$db,$id);
   if(isset($p['skip'])){echo 'PHOTO_DIAG='.json_encode(array('id'=>$id,'skip'=>$p['skip']))."\n";continue;}
   $plan=$methodPlan->invoke(null,$id,$p,$db);
   $rows=array_slice($plan['changes'],0,3);
   foreach($rows as $i=>$row){
      preg_match('~\\[extimg\\]\\s*(https://[^\\s<]+)~',$row['new'],$m);
      if(empty($m[1]))continue;
      $url=$m[1];
      $host=parse_url($url,PHP_URL_HOST);
      echo 'PHOTO_DIAG='.json_encode(array('id'=>$id,'sample'=>$i,'host'=>$host,'head'=>test_link($url,'head',false),
       'get_range'=>test_link($url,'get',false),'get_follow'=>test_link($url,'get',true)),
       JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
   }
 }catch(Throwable $e){echo 'PHOTO_DIAG='.json_encode(array('id'=>$id,'error'=>get_class($e),'message'=>substr($e->getMessage(),0,120)))."\n";}
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
if __name__=="__main__":
    data=execute(remote)
    for line in data.splitlines():
        if line.startswith("PHOTO_DIAG="):print(line,flush=True)
