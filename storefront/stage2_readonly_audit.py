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
PY
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
                if line.startswith("STOREFRONT_STAGE2_"):print(line,flush=True)
            if p.returncode:raise RuntimeError("Stage2 audit failed: "+p.stderr[-800:])
        finally:
            try:
                request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/".format(VM_ID,key_id),"DELETE",None,headers)
                print("temporary_ssh_key_removed=true")
            except Exception:
                print("WARNING: temporary_ssh_key_cleanup_failed")
if __name__=="__main__":run()
