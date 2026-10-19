#!/usr/bin/env python3
"""Read-only final audit: Mebelik legacy links and missing external vs native images."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mbk-end-audit-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$rows=$db->query('SELECT DISTINCT p.id,p.name,p.url,p.summary,p.image_id FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id=28 AND p.type_id=31')->fetchAll();
$stats=array('mapped'=>count($rows),'has_external_links'=>0,'without_external_with_native'=>0,'without_external_without_native'=>0,'legacy_links'=>0);
function probe($url){
 $ch=curl_init($url);
 curl_setopt_array($ch,array(CURLOPT_RETURNTRANSFER=>true,CURLOPT_RANGE=>'0-1023',
 CURLOPT_TIMEOUT=>12,CURLOPT_CONNECTTIMEOUT=>6,CURLOPT_SSL_VERIFYPEER=>true,
 CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_FOLLOWLOCATION=>false,
 CURLOPT_CAINFO=>'/home/web/vm-23f9aff9.na4u.ru/www/wa-config/mebelik-globalsign-root-r46.pem'));
 curl_exec($ch);
 $out=array('status'=>(int)curl_getinfo($ch,CURLINFO_HTTP_CODE),'mime'=>(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE),'errno'=>curl_errno($ch));
 curl_close($ch);return $out;
}
foreach($rows as $r){
 $summary=(string)$r['summary'];
 preg_match_all('~\[extimg\](.*?)\[/extimg\]~isu',$summary,$m);
 if(count($m[1])===0){if((int)$r['image_id']>0)$stats['without_external_with_native']++;else $stats['without_external_without_native']++;continue;}
 $stats['has_external_links']++;
 $legacy=array();
 foreach($m[1] as $u) {
  $url=trim(html_entity_decode($u,ENT_QUOTES,'UTF-8'));
  if(preg_match('~^https?://(www[.])?mebelik[.]ru(?=/|$)~i',$url) && stripos($url,'https://mebelik.ru')!==0)
   $legacy[]=$url;
 }
 if($legacy){
  $stats['legacy_links']++;
  $old=$legacy[0];
  $fixed=preg_replace('~^https?://(www[.])?mebelik[.]ru~i','https://mebelik.ru',$old);
  if($stats['legacy_links']<=8){$altered=preg_replace_callback('~(\\[extimg\\]\\s*)(https?://(?:www[.])?mebelik[.]ru)([^\\s<\\[\\]]*)(\\s*\\[/extimg\\])~iu',function($x){return $x[1].'https://mebelik.ru'.$x[3].$x[4];},$summary);
  echo 'MBK_AUDIT_DIAG='.json_encode(array('id'=>$r['id'],'original'=>$old,'normalized'=>$fixed,'rawprefix'=>substr($summary,0,160),'old_regex_changed'=>($altered!==$summary)),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";}
  echo 'MBK_AUDIT_OLD='.json_encode(array('id'=>(int)$r['id'],'name'=>$r['name'],
   'native_image'=>((int)$r['image_id']>0),'external_count'=>count($m[1]),
   'old_protocol'=>parse_url($old,PHP_URL_SCHEME),'test_canonical'=>probe($fixed)),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
 }
}
echo 'MBK_AUDIT_TOTAL='.json_encode($stats,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
out=execute(remote)
for line in out.splitlines():
 if line.startswith(('MBK_AUDIT_OLD=','MBK_AUDIT_TOTAL=')):print(line,flush=True)
