#!/usr/bin/env python3
"""Read-only Webasyst/frontend post-update check for 33krovati external galleries."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/verify33k-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$total=$db->query("SELECT COUNT(*) total,SUM(p.summary LIKE '%[extimg]%') extimg FROM shop_yml_products m
 JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id=289")->fetch();
echo 'KROVATI_VERIFY_TOTAL='.json_encode(array('mapped'=>(int)$total['total'],'external'=>(int)$total['extimg'])).PHP_EOL;
foreach(array(1490301,1490302,1490533) as $id) {
 $p=$db->query('SELECT id,url,name,summary,status FROM shop_product WHERE id='.(int)$id)->fetch();
 $s=$db->query('SELECT sku FROM shop_product_skus WHERE product_id='.(int)$id.' LIMIT 1')->fetch();
 preg_match_all('~\[extimg\](.*?)\[/extimg\]~is',(string)$p['summary'],$pics);
 $expected=count($pics[1]);
 $url='https://profikompany.ru/'.trim($p['url'],'/').'/';
 $ch=curl_init($url);
 curl_setopt_array($ch,array(CURLOPT_FOLLOWLOCATION=>true,CURLOPT_RETURNTRANSFER=>true,
  CURLOPT_TIMEOUT=>35,CURLOPT_CONNECTTIMEOUT=>10,CURLOPT_SSL_VERIFYPEER=>true));
 $html=curl_exec($ch);$http=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);curl_close($ch);
 $actual=substr_count((string)$html,'data-extimg=');
 $result=array('product_id'=>$id,'sku'=>$s['sku'],'expected_urls'=>$expected,'http'=>$http,
  'thumbnails'=>$actual,'external_main'=>strpos((string)$html,'js-extimg-main-img')!==false,
  'published'=>(int)$p['status']===1);
 echo 'KROVATI_VERIFY_PRODUCT='.json_encode($result,JSON_UNESCAPED_UNICODE).PHP_EOL;
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=1 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("KROVATI_VERIFY_"):print(line,flush=True)
if out.count("KROVATI_VERIFY_PRODUCT=")!=3:raise RuntimeError("Missing photo storefront QA output")
