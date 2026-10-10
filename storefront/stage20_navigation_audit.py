#!/usr/bin/env python3
"""Stage 20: inspect existing pages and manufacturer feature, read-only.

Reads shop_page and brand-feature schema only; no writes.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
p=$(mktemp /tmp/storefront-nav-read-XXXXXX.php)
trap 'rm -f "$p"' EXIT
cat > "$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$tables=array('shop_page','shop_feature','shop_feature_values_varchar','shop_product_features','shop_product');
$out=array();
foreach($tables as $t) {
 try {
  $cols=$m->query("SHOW COLUMNS FROM ".$t)->fetchAll();
  $out[$t]=array('cols'=>array());
  foreach($cols as $c){$out[$t]['cols'][]=$c['Field'];}
 }catch(Throwable $e){$out[$t]=array('error'=>get_class($e));}
}
echo "STORE_NAV_TABLES=".json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
try {
 $rows=$m->query("SELECT id,domain,route,url,name,title,status FROM shop_page WHERE domain = 'profikompany.ru' ORDER BY id LIMIT 100")->fetchAll();
 echo "STORE_NAV_PAGES=".json_encode($rows,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}catch(Throwable $e){ echo "STORE_NAV_PAGES_ERROR=".json_encode($e->getMessage(),JSON_UNESCAPED_UNICODE)."\n"; }
try {
 $rows=$m->query("SELECT id,code,name,type FROM shop_feature WHERE code LIKE '%brand%' OR code LIKE '%manufact%' OR name LIKE '%Бренд%' OR name LIKE '%Производител%' ORDER BY id LIMIT 35")->fetchAll();
 echo "STORE_NAV_FEATURES=".json_encode($rows,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}catch(Throwable $e){echo "STORE_NAV_FEATURE_ERROR=".json_encode($e->getMessage(),JSON_UNESCAPED_UNICODE)."\n";}
try{
 $rows=$m->query("SHOW TABLES LIKE 'shop_brand%'")->fetchAll();
 echo "STORE_NAV_BRAND_TABLES=".json_encode($rows,JSON_UNESCAPED_UNICODE)."\n";
}catch(Throwable $e){}
try{
 $php=$root.'/wa-apps/shop/lib/models/shopPage.model.php';
 echo "STORE_NAV_PAGE_MODEL=".json_encode(array('exists'=>is_file($php),'path'=>$php,'content'=>is_file($php)?substr(file_get_contents($php),0,7000):''),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}catch(Throwable $e){}
try{
 $rows=$m->query("SELECT v.value, COUNT(DISTINCT p.id) AS product_count FROM shop_feature_values_varchar v JOIN shop_product_features pf ON pf.feature_value_id=v.id AND pf.feature_id=9 JOIN shop_product p ON p.id=pf.product_id AND p.status=1 WHERE v.feature_id=9 GROUP BY v.value ORDER BY product_count DESC LIMIT 90")->fetchAll();
 echo "STORE_NAV_LIVE_MANUFACTURERS=".json_encode($rows,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\\n";
 $count=$m->query("SELECT COUNT(*) AS qty FROM shop_feature_values_varchar WHERE feature_id=9")->fetchAll();
 echo "STORE_NAV_MANUFACTURERS_TOTAL=".json_encode($count,JSON_UNESCAPED_UNICODE)."\\n";
}catch(Throwable $e){echo "STORE_NAV_MANUFACTURERS_ERROR=".json_encode($e->getMessage(),JSON_UNESCAPED_UNICODE)."\\n";}
try {
 $existing=$m->query("SELECT id,parent_id,domain,route,url,full_url,name,title,create_datetime,update_datetime,create_contact_id,sort,status,thumbpage,CHAR_LENGTH(content) AS content_length FROM shop_page WHERE id IN (7,10,12,20) ORDER BY id")->fetchAll();
 echo "STORE_NAV_PAGE_SHAPES=".json_encode($existing,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\\n";
}catch(Throwable $e){echo "STORE_NAV_PAGE_SHAPES_ERROR=".json_encode($e->getMessage(),JSON_UNESCAPED_UNICODE)."\\n";}
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php -d display_errors=1 -d log_errors=0 '$p'"
"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-stage20-nav-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-stage20-nav-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('STORE_NAV_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
