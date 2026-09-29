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
echo "--- HOOK SOURCES ---"
grep -RIn "backend_prod_list\|backend_products" "$ROOT/wa-apps/shop" "$ROOT/wa-system" 2>/dev/null | head -n 120 || true
echo "--- HOOK SOURCES END ---"
echo "--- NEW LIST ACTION EVENT ---"
sed -n '165,225p' "$ROOT/wa-apps/shop/lib/actions/prod/main/shopProdList.action.php" || true
echo "--- NEW LIST TEMPLATE ROWS ---"
sed -n '1,220p' "$ROOT/wa-apps/shop/templates/actions/prod/main/List.html" || true
echo "--- LEGACY PRODUCT LIST TEMPLATE ROWS ---"
grep -RIn "product-list\|s-product-list\|product-image\|image_id\|thumb" "$ROOT/wa-apps/shop/templates/actions/products" "$ROOT/wa-apps/shop/templates/actions-legacy/products" 2>/dev/null | head -n 160 || true
echo "--- NEW IMAGE COLUMN JS EXACT ---"
sed -n '3480,3575p' "$ROOT/wa-apps/shop/js/backend/products/main/main.list.js" || true
sed -n '3860,4020p' "$ROOT/wa-apps/shop/js/backend/products/main/main.list.js" || true
echo "--- NEW PRODUCT COLUMN TEMPLATES ---"
grep -n "component-product-column" "$ROOT/wa-apps/shop/templates/actions/prod/main/List.html" | head -n 120 || true
echo "--- NEW IMAGE COMPONENT MARKUP ---"
grep -n "sku_mod_photo\|component-product-column-image\|dummy_image_url\|s-image-wrapper\|s-photo" "$ROOT/wa-apps/shop/templates/actions/prod/main/List.html" | head -n 220 || true
grep -n "component-product-column-image\|sku_mod_photo" "$ROOT/wa-apps/shop/js/backend/products/main/main.list.js" | head -n 80 || true
for L in $(grep -n "sku_mod_photo" "$ROOT/wa-apps/shop/templates/actions/prod/main/List.html" | head -n 5 | cut -d: -f1); do S=$((L-35)); E=$((L+55)); sed -n "${S},${E}p" "$ROOT/wa-apps/shop/templates/actions/prod/main/List.html"; done
echo "--- OLD LOAD LIST CONTROLLER ---"
sed -n '1,220p' "$ROOT/wa-apps/shop/lib/actions/products/shopProductsLoadList.controller.php" || true
echo "--- FORMAT PRODUCT RETURN ---"
sed -n '620,760p' "$ROOT/wa-apps/shop/lib/actions/prod/main/shopProdList.action.php" || true
echo "--- NEW JS PHOTO COMPUTED ---"
sed -n '3925,3985p' "$ROOT/wa-apps/shop/js/backend/products/main/main.list.js" || true
echo "--- PRESENTATION GETPRODUCTS SUMMARY ---"
grep -RIn "function getProducts" "$ROOT/wa-apps/shop/lib" 2>/dev/null | grep -i presentation | head -n 20 || true
grep -RIn "summary" "$ROOT/wa-apps/shop/lib/classes" "$ROOT/wa-apps/shop/lib/model" 2>/dev/null | grep -i presentation | head -n 80 || true
echo "--- LEGACY LIST ACTION DATA ---"
grep -RIn "class shopProducts.*List\|function execute" "$ROOT/wa-apps/shop/lib/actions/products" 2>/dev/null | head -n 80 || true
ls "$ROOT/wa-apps/shop/lib/actions/products" | grep -i list || true
echo "--- FORMAT PRODUCTS METHOD ---"
grep -n "function formatProducts" "$ROOT/wa-apps/shop/lib/actions/prod/main/shopProdList.action.php" || true
LINE=$(grep -n "function formatProducts" "$ROOT/wa-apps/shop/lib/actions/prod/main/shopProdList.action.php" | head -n1 | cut -d: -f1 || true)
if [ -n "$LINE" ]; then START=$((LINE-10)); END=$((LINE+180)); sed -n "${START},${END}p" "$ROOT/wa-apps/shop/lib/actions/prod/main/shopProdList.action.php"; fi
echo "--- NEW TEMPLATE PHOTO USAGE ---"
grep -n "photos\|photo_url\|image_url\|dummy_image\|image_id\|s-photo-wrapper" "$ROOT/wa-apps/shop/templates/actions/prod/main/List.html" | head -n 180 || true
echo "--- LEGACY TABLE IMAGE TEMPLATE ---"
sed -n '74,145p' "$ROOT/wa-apps/shop/templates/actions/products/product_list_table.html" || true
echo "--- LEGACY THUMBS IMAGE TEMPLATE ---"
sed -n '1,55p' "$ROOT/wa-apps/shop/templates/actions/products/product_list_thumbs.html" || true
echo "--- LIST JS SELECTORS ---"
grep -RIn "data-product-id\|s-product-item\|product_id\|image" "$ROOT/wa-apps/shop/js/backend/products" "$ROOT/wa-apps/shop/js/backend/prod" 2>/dev/null | head -n 200 || true
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
