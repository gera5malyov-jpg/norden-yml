#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/webasyst-thumb-source.txt")

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"wa-thumb-probe/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"wa-thumb-probe-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            remote=r'''set -e
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cat >/tmp/wa_thumb_probe.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$skus=array('DEEP-59179','DEEP-59180','DEEP-59176','DEEP-59177','DEEP-61461','AF-31662421','AF-31655692');
foreach($skus as $sku){
  $r=$m->query(
    "SELECT p.id,p.name,p.summary,p.description,p.image_id,s.id sku_id,s.sku
     FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id
     WHERE s.sku=s:sku LIMIT 1",
     array('sku'=>$sku)
  )->fetch();
  if(!$r){echo "SKU=".$sku." NOT_FOUND\n"; continue;}
  echo "SKU=".$sku." PID=".$r['id']." IMAGE_ID=".ifset($r['image_id'])."\n";
  echo "SUMMARY=".str_replace(array("\r","\n"),array("","\\n"),substr((string)$r['summary'],0,1200))."\n";
  echo "DESCRIPTION=".str_replace(array("\r","\n"),array("","\\n"),substr((string)$r['description'],0,600))."\n";
  $imgs=$m->query("SELECT id,product_id,ext,original_filename,sort FROM shop_product_images WHERE product_id=i:id ORDER BY sort,id LIMIT 3",array('id'=>(int)$r['id']))->fetchAll();
  echo "NATIVE_IMAGES=".json_encode($imgs,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
PHP
chown web:web /tmp/wa_thumb_probe.php
timeout 20s su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/wa_thumb_probe.php'
rm -f /tmp/wa_thumb_probe.php
'''
            p=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=12",f"root@{VM_IP}","bash -s"],
                input=remote,text=True,capture_output=True,timeout=35)
            out=p.stdout
            if p.stderr: out+="\nSTDERR\n"+p.stderr
            REPORT.write_text(out,encoding="utf-8")
            print(out)
            if p.returncode: raise RuntimeError("remote exit "+str(p.returncode))
        finally:
            try:
                req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception:
                pass

if __name__=="__main__":
    main()
