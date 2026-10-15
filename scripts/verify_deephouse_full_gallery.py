#!/usr/bin/env python3
"""Read-only post-deploy Deephouse gallery and media-preservation verification."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/deephouse-gallery-qa-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat > "$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$a=$db->query("SELECT COUNT(*) mapped, SUM(p.summary LIKE '%[extimg]%') with_external, SUM(p.image_id>0) native,
 SUM(p.status=1) published FROM shop_yml_products yp JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=20")->fetch();
echo 'DEEPHOUSE_QA_TOTALS='.json_encode(array('mapped'=>(int)$a['mapped'],
 'external'=>(int)$a['with_external'],'native'=>(int)$a['native'],'published'=>(int)$a['published']))."\n";
if((int)$a['mapped']<1800 || (int)$a['with_external']<1880)throw new RuntimeException('Unexpected summary coverage');
foreach(array(1223042,1223043,1223044,1223050,218600) as $id){
 $p=$db->query('SELECT id,url,summary,image_id,status FROM shop_product WHERE id='.(int)$id)->fetch();
 if(!$p)throw new RuntimeException('Missing sampled product');
 $sum=(string)$p['summary'];
 preg_match_all('~\\[extimg\\](.*?)\\[/extimg\\]~is',$sum,$all);
 $blockCount=count($all[1]);
 $urls=array();
 foreach($all[1] as $block)foreach(preg_split('/\\r?\\n/',trim($block)) as $line){$u=trim($line);if(strpos($u,'https://')===0 && !in_array($u,$urls,true))$urls[]=$u;}
 $url='https://profikompany.ru/'.ltrim($p['url'],'/').'/';
 $c=curl_init($url);curl_setopt_array($c,array(CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>true,CURLOPT_MAXREDIRS=>3,CURLOPT_TIMEOUT=>28,CURLOPT_SSL_VERIFYPEER=>false));
 $html=curl_exec($c);$code=curl_getinfo($c,CURLINFO_HTTP_CODE);curl_close($c);
 $thumbs=substr_count((string)$html,'data-extimg=');
 $visible=strpos((string)$html,'js-extimg-main-img')!==false;
 $good=$code===200 && $visible && $thumbs===count($urls) && $thumbs>=1;
 echo 'DEEPHOUSE_QA_PRODUCT='.json_encode(array('id'=>$id,'http'=>$code,'blocks'=>$blockCount,
  'unique_external_urls'=>count($urls),'gallery_thumbs'=>$thumbs,'native'=>(int)$p['image_id'],
  'gallery_ok'=>$good),JSON_UNESCAPED_UNICODE)."\n";
 if(!$good)throw new RuntimeException('Product gallery validation failed for id '.$id);
}
echo 'DEEPHOUSE_QA_RESULT=success'.PHP_EOL;
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=1 '$F'"
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("DEEPHOUSE_QA_"):print(line,flush=True)
if "DEEPHOUSE_QA_RESULT=success" not in out:raise RuntimeError("Live QA incomplete")
