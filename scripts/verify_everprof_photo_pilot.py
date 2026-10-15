#!/usr/bin/env python3
"""Read-only live acceptance for five EverProf galleries after image pilot."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'storefront'))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/verify-everprof-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
chdir('/home/web/vm-23f9aff9.na4u.ru/www');
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
foreach(array(1199339,1199328,1458758,1458759,1458760,1225888) as $pid){
 $p=$db->query('SELECT id,url,name,summary,image_id,status FROM shop_product WHERE id='.(int)$pid)->fetch();
 if(!$p)throw new RuntimeException('Missing tested product '.$pid);
 $summary=(string)$p['summary'];
 preg_match_all('~\\[extimg\\](.*?)\\[/extimg\\]~is',$summary,$ms);
 $count=count($ms[1]);$first=$count?trim($ms[1][0]):'';
 $url='https://profikompany.ru/'.trim($p['url'],'/').'/';
 $ch=curl_init($url);
 curl_setopt_array($ch,array(CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>true,CURLOPT_MAXREDIRS=>5,CURLOPT_TIMEOUT=>30,CURLOPT_SSL_VERIFYPEER=>false));
 $html=curl_exec($ch);$code=curl_getinfo($ch,CURLINFO_HTTP_CODE);$end=curl_getinfo($ch,CURLINFO_EFFECTIVE_URL);curl_close($ch);
 $actual=$html?substr_count($html,'data-extimg='):0;
 $out=array('id'=>$pid,'http'=>(int)$code,'summary_photos'=>$count,
   'gallery_thumbnails'=>$actual,'route'=>$p['url'],'effective_route'=>parse_url($end,PHP_URL_PATH),'product_page'=>strpos((string)$html,'class="product-page"')!==false,'external_gallery'=>strpos((string)$html,'product-gallery--extimg')!==false,'native_gallery'=>strpos((string)$html,'product-gallery--extimg')===false && strpos((string)$html,'product-gallery')!==false,'main_image'=>strpos((string)$html,'js-extimg-main-img')!==false,
   'native_image_unchanged'=>(int)$p['image_id']>0,'published'=>(int)$p['status']===1);
 echo 'YMLPHOTO_LIVE='.json_encode($out,JSON_UNESCAPED_UNICODE)."\n";
}
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
r=execute(REMOTE)
for l in r.splitlines():
 if l.startswith('YMLPHOTO_LIVE='):print(l,flush=True)
if r.count('YMLPHOTO_LIVE=') != 6:raise RuntimeError('Not all product checks returned')
