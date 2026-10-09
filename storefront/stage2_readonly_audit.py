#!/usr/bin/env python3
"""Read-only server audit of product recommendations and checkout templates."""
from __future__ import annotations
import os
import json
import subprocess
import tempfile
import urllib.parse
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE = r"""set -eu
python3 - <<'PY'
import pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
files=[
  ('wa-data/public/shop/themes/pureMegapolis42/product.info.html',390,421),
  ('wa-data/public/shop/themes/pureMegapolis42/order.html',1,160),
  ('wa-data/public/site/themes/pureMegapolis42/layouts/layout.sets.html',1,95)
]
for rel,first,last in files:
  p=root/rel
  if not p.is_file(): continue
  lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
  rows=[{'n':i+1,'line':lines[i][:450]} for i in range(first-1,min(len(lines),last))]
  print('STOREFRONT_STAGE2_TEMPLATE='+json.dumps({'file':rel,'lines':rows},ensure_ascii=False))
for rel_dir in ('wa-apps/shop/lib','wa-system/webasyst/lib','wa-apps/shop/templates','wa-system/auth','wa-config'):
  rp=root/rel_dir
  if not rp.is_dir():continue
  try:
    import subprocess
    p=subprocess.Popen(['grep','-RIl','--include=*.php','--include=*.html','-E',
      'shipping_agreement|service_agreement|getDocumentId\\(',str(rp)],
      stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stdout,stderr=p.communicate(timeout=12)
    print('STOREFRONT_CONSENT_SOURCE='+json.dumps({'directory':rel_dir,'files':[x.replace(str(root)+'/','') for x in stdout.decode('utf-8','replace').splitlines()[:30]]},ensure_ascii=False))
  except Exception as e:
    try:p.kill()
    except Exception:pass
    print('STOREFRONT_CONSENT_SOURCE='+json.dumps({'directory':rel_dir,'error':type(e).__name__},ensure_ascii=False))
for rel in ('wa-config/apps/shop/checkout2.php','wa-config/apps/shop/checkout.php'):
  file=root/rel
  if file.is_file():
    data=file.read_text(encoding='utf-8',errors='replace')
    print('STOREFRONT_CONFIG_LINK_COUNTS='+json.dumps({
      'file':rel,
      'bad_prof_link_count':data.count('---https://profikompany.ru/dostavka/---'),
      'empty_privacy_link_count':data.count('<a href="">условиями обработки персональных данных</a>'),
      'privacy_link_count':data.count('/privacy-policy/')
    },ensure_ascii=False))
for rel in (
  'wa-apps/shop/lib/classes/checkout2/shopCheckoutConfig.class.php',
  'wa-apps/shop/lib/classes/checkout2/shopCheckoutAuthStep.class.php',
  'wa-apps/shop/lib/classes/checkout2/shopCheckoutRegionStep.class.php',
  'wa-system/webasyst/lib/classes/webasystHelper.class.php',
  'wa-config/apps/shop/checkout2.php',
  'wa-config/apps/shop/checkout.php',
  'wa-config/auth.php'):
  p=root/rel
  if not p.is_file():continue
  ls=p.read_text(encoding='utf-8',errors='replace').splitlines()
  hits=[]
  for i,line in enumerate(ls):
    if any(x in line.lower() for x in ('service_agreement','shipping_agreement','getdocumentid','agreement_document','---https://profikompany.ru/dostavka/---')):
      if rel.startswith('wa-config'):
        hits.append({'n':i+1,'contains_bad_url':'---https://profikompany.ru/dostavka/---' in line,
          'length':len(line),'key_only':line.split('=>')[0][:90]})
      else:
        hits.append({'n':i+1,'lines':[{'n':j+1,'v':ls[j][:270]} for j in range(max(0,i-3),min(len(ls),i+6))]})
  if hits: print('STOREFRONT_CONSENT_LINES='+json.dumps({'file':rel,'matches':hits[:15]},ensure_ascii=False))
for rel in ('wa-system/webasyst/lib/models/waAgreementDocument.model.php',):
  path=root/rel
  if path.is_file():
    ls=path.read_text(encoding='utf-8',errors='replace').splitlines()
    print('STOREFRONT_AGREEMENT_MODEL='+json.dumps({'file':rel,'lines':[{'n':i+1,'v':ls[i][:290]} for i in range(min(180,len(ls)))]},ensure_ascii=False))
for relative in ('wa-system/webasyst','wa-apps/shop/lib'):
  import subprocess
  p=subprocess.Popen(['grep','-Rln','--include=*.php','getAgreementDocument',str(root/relative)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  out,_=p.communicate()
  print('STOREFRONT_AGREEMENT_GETTERS='+json.dumps({'root':relative,'paths':out.decode('utf-8','replace').splitlines()[:20]},ensure_ascii=False))
for d in (root/'wa-data/public/site/themes/pureMegapolis42', root/'wa-data/public/shop/themes/pureMegapolis42'):
  if not d.is_dir():continue
  for p in d.rglob('*.html'):
    if not p.is_file() or p.stat().st_size>100000:continue
    lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
    hits=[{'n':i+1,'line':line[:420]} for i,line in enumerate(lines)
          if any(w.lower() in line.lower() for w in ('условия оплаты','условия доставки','политика обработки','footer__link'))]
    if hits:print('STOREFRONT_LEGAL_TEMPLATE='+json.dumps({'file':str(p.relative_to(root)),'lines':hits[:12]},ensure_ascii=False))
PY
printf 'STOREFRONT_AGREEMENT_SOURCES='
grep -RIl --include='*.php' -E 'wa_agreement_document|class waAgreementDocument' /home/web/vm-23f9aff9.na4u.ru/www/wa-system /home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/lib 2>/dev/null | head -18 | tr '\n' ',' || true
echo
printf 'STOREFRONT_CACHE_DIRS='
find /home/web/vm-23f9aff9.na4u.ru/www/wa-cache -maxdepth 2 -type d 2>/dev/null | head -25 | tr '\n' ',' || true
echo
tmp=$(mktemp /tmp/storefront-stage2-read-XXXXXX.php)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
try {
  $diag=array();
  $diag['settings_columns']=$m->query("SHOW COLUMNS FROM wa_app_settings")->fetchAll();
  $diag['page_columns']=$m->query("SHOW COLUMNS FROM site_page")->fetchAll();
  $diag['auth_tables']=array();
  foreach($m->query("SHOW TABLES")->fetchAll() as $r){foreach($r as $name){
    if(stripos((string)$name,'auth')!==false || stripos((string)$name,'checkout')!==false)
      $diag['auth_tables'][]=$name;
  }}
  $diag['settings_public_policies']=array();
  $cols=array();
  foreach($diag['settings_columns'] as $row){$cols[]=$row['Field'];}
  $appCol=in_array('app_id',$cols)?'app_id':(in_array('app',$cols)?'app':null);
  if($appCol && in_array('name',$cols) && in_array('value',$cols)){
    foreach($m->query("SELECT ". $appCol ." AS app, name, value FROM wa_app_settings
      WHERE name LIKE '%agreement%' OR name LIKE '%privacy%' OR name LIKE '%policy%'
      ORDER BY app,name LIMIT 60")->fetchAll() as $row){
      $val=(string)$row['value'];
      $diag['settings_public_policies'][]=array('app'=>$row['app'],'name'=>$row['name'],
        'value_if_public_url'=>(strlen($val)<350 && strpos($val,'http')!==false)?$val:null,
        'value_length'=>strlen($val));
    }
  }
  $diag['existing_shop_policy_pages']=array();
  try {
    $diag['shop_page_columns']=$m->query("SHOW COLUMNS FROM shop_page")->fetchAll();
    $diag['existing_shop_policy_pages']=$m->query("SELECT id,name,url,title FROM shop_page
      WHERE LOWER(name) LIKE '%политик%' OR LOWER(name) LIKE '%персон%'
         OR LOWER(name) LIKE '%конфиденц%' OR LOWER(name) LIKE '%оферт%'
         OR LOWER(url) LIKE '%policy%' OR LOWER(url) LIKE '%privac%'
      LIMIT 30")->fetchAll();
  } catch(Throwable $e){$diag['shop_page_error']=get_class($e).': '.$e->getMessage();}
  $diag['malformed_agreement_settings']=array();
  try{
    $diag['malformed_agreement_settings']=$m->query(
      "SELECT app_id,name,CHAR_LENGTH(value) AS value_length FROM wa_app_settings
       WHERE value LIKE '%---https://profikompany.ru/dostavka/---%' LIMIT 30")->fetchAll();
  } catch(Throwable $e){$diag['malformed_settings_error']=get_class($e).': '.$e->getMessage();}
  $diag['pages_meta']=array();
  try{
    $diag['shop_info_pages']=$m->query("SELECT id,domain,route,full_url,url,name,status FROM shop_page WHERE LOWER(url) LIKE '%dostav%' OR LOWER(url) LIKE '%oplat%' OR LOWER(name) LIKE '%достав%' OR LOWER(name) LIKE '%оплат%' LIMIT 20")->fetchAll();
    $diag['site_info_pages']=$m->query("SELECT id,domain_id,route,full_url,url,name,status FROM site_page WHERE LOWER(url) LIKE '%dostav%' OR LOWER(url) LIKE '%oplat%' OR LOWER(name) LIKE '%достав%' OR LOWER(name) LIKE '%оплат%' LIMIT 20")->fetchAll();
    $diag['domain_examples']=$m->query("SELECT id,name FROM site_domain ORDER BY id LIMIT 20")->fetchAll();
    $diag['agreement_like_tables']=array();
    foreach($m->query("SHOW TABLES")->fetchAll() as $r){foreach($r as $table){
      if(stripos((string)$table,'setting')!==false || stripos((string)$table,'agreement')!==false) $diag['agreement_like_tables'][]=$table;
    }}
    $diag['config_related_settings']=$m->query("SELECT app_id,name,CHAR_LENGTH(value) AS value_length FROM wa_app_settings WHERE name LIKE '%auth%' OR name LIKE '%consent%' OR name LIKE '%agreement%' ORDER BY app_id,name LIMIT 100")->fetchAll();
  }catch(Throwable $e){$diag['pages_meta_error']=get_class($e).': '.$e->getMessage();}
  $diag['agreement_native']=array();
  try{
    foreach(array('wa_agreement_document','wa_contact_settings','shop_checkout_flow') as $tn){
      $columns=$m->query("SHOW COLUMNS FROM `".$tn."`")->fetchAll();
      $names=array();
      foreach($columns as $col){$names[]=$col['Field'];}
      $allow=array('id','name','title','type','url','domain','route','app_id','status','scope','version','contact_id','key');
      $safe=array_intersect($allow,$names);
      $rows=$safe ? $m->query("SELECT ".implode(',',$safe)." FROM `".$tn."` LIMIT 35")->fetchAll() : array();
      $diag['agreement_native'][$tn]=array('columns'=>$names,'rows'=>$rows);
    }
  }catch(Throwable $e){$diag['agreement_native_error']=get_class($e).': '.$e->getMessage();}
  $diag['shop_page_example']=$m->query("SELECT id,name,url,full_url,domain,route,status,create_contact_id,sort,parent_id
      FROM shop_page WHERE domain='profikompany.ru' AND route='*' ORDER BY id LIMIT 12")->fetchAll();
  $diag['agreement_documents']=array();
  try {
    $diag['agreement_documents']=$m->query(
      "SELECT id,app_id,context,domain,locale,document_name,CHAR_LENGTH(document_text) AS content_length,
       LEFT(document_text,400) AS preview,
       LOCATE('---https://profikompany.ru/dostavka/---',document_text) AS broken_link_pos,
       LOCATE('privacy-policy/',document_text) AS fixed_link_pos
       FROM wa_agreement_document WHERE domain='profikompany.ru' ORDER BY id")->fetchAll();
  }catch(Throwable $e){$diag['agreement_docs_error']=get_class($e).': '.$e->getMessage();}
  $diag['existing_public_pages']=array();
  $pageCols=array();
  foreach($diag['page_columns'] as $row){$pageCols[]=$row['Field'];}
  if(in_array('url',$pageCols) && in_array('name',$pageCols)){
    $diag['existing_public_pages']=$m->query("SELECT name,url FROM site_page
      WHERE LOWER(name) LIKE '%персон%' OR LOWER(name) LIKE '%политик%'
      OR LOWER(url) LIKE '%privacy%' OR LOWER(url) LIKE '%offer%'
      LIMIT 40")->fetchAll();
  }
  echo 'STOREFRONT_LEGAL_DB='.json_encode($diag,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\\n";
} catch(Throwable $e){echo 'STOREFRONT_LEGAL_DB_ERROR='.get_class($e).': '.$e->getMessage()."\\n";}
$out=array('samples'=>array());
$ids=array(1489211,392940,763100,1186404,148303);
foreach($ids as $id){
  $p=$m->query("SELECT p.id,p.name,p.category_id,p.type_id,p.status,p.count
    FROM shop_product p WHERE p.id=i:id",array('id'=>$id))->fetch();
  if(!$p){continue;}
  $entry=array('product'=>$p,'cross'=>array(),'up'=>array());
  try{
    $product=new shopProduct($id);
    foreach(array('cross'=>'crossSelling','up'=>'upSelling') as $k=>$method){
      $items=$product->$method(12);
      foreach($items as $candidate){
        if(!isset($candidate['id']))continue;
        $entry[$k][]=array('id'=>(int)$candidate['id'],
          'name'=>(string)ifset($candidate['name'],''),
          'category_id'=>(int)ifset($candidate['category_id'],0),
          'type_id'=>(int)ifset($candidate['type_id'],0));
      }
    }
  }catch(Throwable $e){$entry['recommendation_error']=get_class($e).': '.$e->getMessage();}
  $out['samples'][]=$entry;
}
try{
  $out['catalog_coverage']=$m->query("SELECT COUNT(*) AS public_products,
    SUM(CASE WHEN category_id IS NULL OR category_id=0 THEN 1 ELSE 0 END) AS no_primary_category
    FROM shop_product WHERE status=1")->fetch();
  $out['sample_category_membership']=$m->query("SELECT product_id,category_id
    FROM shop_category_products WHERE product_id IN (1489211,392940,763100,1186404,148303)
    ORDER BY product_id,category_id LIMIT 100")->fetchAll();
  $out['type_upselling']=$m->query("SELECT id,name,upselling FROM shop_type
    WHERE id IN (21,23,124,57) ORDER BY id")->fetchAll();
  $out['no_primary_categories_by_type']=$m->query("SELECT type_id,COUNT(*) AS products
    FROM shop_product WHERE status=1 AND (category_id IS NULL OR category_id=0)
    GROUP BY type_id ORDER BY products DESC LIMIT 15")->fetchAll();
}catch(Throwable $e){$out['catalog_coverage_error']=get_class($e).': '.$e->getMessage();}
try{
 $out['theme_table_names']=array();
 foreach($m->query("SHOW TABLES")->fetchAll() as $r){
   foreach($r as $name){if(stripos((string)$name,'theme')!==false)$out['theme_table_names'][]=$name;}
 }
}catch(Throwable $e){}
echo 'STOREFRONT_STAGE2_DB='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$tmp"
su -s /bin/bash web -c "php $tmp"
"""

def run():
    api_key=os.environ.get("NETANGELS_API_KEY","").strip()
    if not api_key: raise RuntimeError("NETANGELS_API_KEY missing")
    gateway=request_json("https://panel.netangels.ru/api/gateway/token/","POST",
        urllib.parse.urlencode({"api_key":api_key}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"storefront-stage2/1.0"})
    token=gateway.get("token")
    if not token: raise RuntimeError("NetAngels token unavailable")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory(prefix="storefront-stage2-") as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=open(key+".pub",encoding="utf-8").read().strip()
        entry=request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/".format(VM_ID),
            "POST",json.dumps({"key":pub,"name":"storefront-stage2-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        key_id=entry.get("id")
        if not key_id: raise RuntimeError("SSH key could not be registered")
        try:
            p=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                 "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
                 "root@"+VM_IP,"bash -s"],
                 input=REMOTE,text=True,capture_output=True,timeout=160)
            for line in p.stdout.splitlines():
                if line.startswith("STOREFRONT_"):print(line,flush=True)
            if p.returncode:raise RuntimeError("Stage2 audit failed: "+p.stderr[-800:])
        finally:
            try:
                request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/".format(VM_ID,key_id),"DELETE",None,headers)
                print("temporary_ssh_key_removed=true")
            except Exception:
                print("WARNING: temporary_ssh_key_cleanup_failed")
if __name__=="__main__":run()
