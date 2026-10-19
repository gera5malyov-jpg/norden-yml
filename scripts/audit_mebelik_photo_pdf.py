#!/usr/bin/env python3
"""Read-only audit of Mebelik photos and accidentally embedded PDF URLs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

remote=r"""set -euo pipefail
cd /home/web/vm-23f9aff9.na4u.ru/www
F="$(mktemp /tmp/mebelik-linkaudit-XXXXXX.php)"
trap 'rm -f "$F"' EXIT
cat >"$F" <<'PHP'
<?php
error_reporting(E_ALL & ~E_NOTICE & ~E_DEPRECATED);
require_once 'wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');
$db=new waModel();
$products=$db->query('SELECT DISTINCT p.id,p.url,p.name,p.summary,p.description,p.image_id FROM shop_yml_products m JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id=28 AND p.type_id=31')->fetchAll();
$tot=array('products'=>count($products),'products_with_extimg'=>0,'products_without_extimg'=>0,'products_with_pdf_in_extimg'=>0,
 'products_with_pdf_in_description'=>0,'products_with_nonphoto_extimg'=>0,'products_with_http_extimg'=>0,
 'extimg_urls'=>0,'unique_extimg_urls'=>0,'pdf_extimg_urls'=>0,'other_nonimage_ext'=>0,
 'pdf_description_urls'=>0,'invalid_extimg_tokens'=>0,'plain_http_extimg'=>0,'missing_native_image'=>0);
$unique=array();
$examples=array();
$badext=array('pdf','doc','docx','xls','xlsx','zip','rar','txt','xml','yml','mp4','svg','html','htm');
foreach($products as $p){
 $s=(string)$p['summary'];
 $d=(string)$p['description'];
 if((int)$p['image_id']<=0)$tot['missing_native_image']++;
 preg_match_all('~\[extimg\]([\s\S]*?)\[/extimg\]~iu',$s,$blocks);
 if(count($blocks[1]))$tot['products_with_extimg']++;
 else $tot['products_without_extimg']++;
 $pdfimg=false;$nonimg=false;$http=false;
 foreach($blocks[1] as $b){
  preg_match_all('~https?://[^\s<>"\'\[\]]+~iu',$b,$matches);
  if(!$matches[0] && trim($b)!=='')$tot['invalid_extimg_tokens']++;
  foreach($matches[0] as $url) {
   $url=html_entity_decode(trim($url),ENT_QUOTES,'UTF-8');
   $url=rtrim($url,',;');
   $tot['extimg_urls']++;$unique[$url]=true;
   $path=parse_url($url,PHP_URL_PATH);
   $ext=strtolower(pathinfo((string)$path,PATHINFO_EXTENSION));
   if(preg_match('~^http://~i',$url)){$http=true;$tot['plain_http_extimg']++;}
   if($ext==='pdf'){$pdfimg=true;$tot['pdf_extimg_urls']++;if(count($examples)<30)$examples[]=array('kind'=>'pdf_extimg','id'=>(int)$p['id'],'slug'=>$p['url'],'file'=>$url);}
   elseif(in_array($ext,$badext,true)){$nonimg=true;$tot['other_nonimage_ext']++;if(count($examples)<30)$examples[]=array('kind'=>'nonimage_ext','id'=>(int)$p['id'],'slug'=>$p['url'],'file'=>$url);}
  }
 }
 if($pdfimg)$tot['products_with_pdf_in_extimg']++;
 if($nonimg)$tot['products_with_nonphoto_extimg']++;
 if($http)$tot['products_with_http_extimg']++;
 $full=$s."\n".$d;
 preg_match_all('~https?://[^\s<>"\'\[\]]+\.pdf(?:\?[^\s<>"\']*)?~iu',$full,$pdfs);
 if(count($pdfs[0])) {
  $tot['products_with_pdf_in_description']++;
  $tot['pdf_description_urls']+=count($pdfs[0]);
  if(count($examples)<30)$examples[]=array('kind'=>'pdf_any','id'=>(int)$p['id'],'slug'=>$p['url'],'file'=>mb_substr($pdfs[0][0],0,330));
 }
}
$tot['unique_extimg_urls']=count($unique);
echo 'MBK_PDF_AUDIT_SUMMARY='.json_encode($tot,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
foreach($examples as $x)echo 'MBK_PDF_AUDIT_EXAMPLE='.json_encode($x,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
$types=array();$all=array_keys($unique);shuffle($all);
foreach(array_slice($all,0,80) as $url){
 $p=parse_url($url);$host=isset($p['host'])?$p['host']:'';
 if(!in_array(strtolower($host),array('mebelik.ru','www.mebelik.ru'),true))continue;
 $ch=curl_init($url);
 curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>false,
  CURLOPT_CONNECTTIMEOUT=>4,CURLOPT_TIMEOUT=>7,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,
  CURLOPT_CAINFO=>'/home/web/vm-23f9aff9.na4u.ru/www/wa-config/mebelik-globalsign-root-r46.pem'));
 curl_exec($ch);
 $st=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
 $mime=strtolower((string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE));
 $errno=curl_errno($ch);
 curl_close($ch);
 $key=$st.'|'.strtok($mime,';').'|'.$errno;
 if(!isset($types[$key]))$types[$key]=0;
 $types[$key]++;
 if(!($st==200 && strpos($mime,'image/')===0) && count($examples)<45)$examples[]=array('kind'=>'sample_bad_response','code'=>$st,'mime'=>$mime,'errno'=>$errno,'file'=>$url);
}
echo 'MBK_PDF_AUDIT_SAMPLE_RESPONSES='.json_encode($types,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
foreach(array_slice($examples,30) as $x)echo 'MBK_PDF_AUDIT_EXAMPLE='.json_encode($x,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
PHP
chmod 644 "$F"
su -s /bin/bash web -c "php -d display_errors=0 '$F'"
"""
result=execute(remote)
for line in result.splitlines():
 if line.startswith(('MBK_PDF_AUDIT_SUMMARY=','MBK_PDF_AUDIT_EXAMPLE=','MBK_PDF_AUDIT_SAMPLE_RESPONSES=')):
  print(line,flush=True)
if 'MBK_PDF_AUDIT_SUMMARY=' not in result:raise RuntimeError('Mebelik audit did not return a summary')
