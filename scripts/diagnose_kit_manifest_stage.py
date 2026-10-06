#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/kit-manifest-stage.txt")

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"kit-stage/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"kit-stage-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            remote=r'''set -euo pipefail
cat >/tmp/ms_kit_stage.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
function stage($name, $fn) {
    echo "STAGE=".$name." BEGIN\n";
    try {
        $r=$fn();
        if (is_array($r)) echo "RESULT_COUNT=".count($r)."\n";
        elseif (is_object($r)) echo "RESULT_OBJECT=".get_class($r)."\n";
        elseif ($r !== null) echo "RESULT=".substr((string)$r,0,500)."\n";
        echo "STAGE=".$name." OK\n";
        return $r;
    } catch (Throwable $e) {
        echo "STAGE=".$name." FAIL ".get_class($e).": ".$e->getMessage()."\n";
        echo $e->getTraceAsString()."\n";
        exit(2);
    }
}
$link_model=stage('link_model', function(){ return new shopMegasuppliersProductModel(); });
$links=stage('links', function() use ($link_model) {
    return $link_model->query("SELECT supplier_sku,product_id,sku_id,purchase_price,stock FROM shop_megasuppliers_product WHERE supplier_id=1 ORDER BY id LIMIT 1")->fetchAll();
});
$link=$links[0];
$product_id=(int)$link['product_id'];
$sku_id=(int)$link['sku_id'];
stage('ids', function() use ($product_id,$sku_id){ return array('product_id'=>$product_id,'sku_id'=>$sku_id); });
$product_model=stage('product_model', function(){ return new shopProductModel(); });
$sku_model=stage('sku_model', function(){ return new shopProductSkusModel(); });
$category_product_model=stage('category_product_model', function(){ return new shopCategoryProductsModel(); });
$category_model=stage('category_model', function(){ return new shopCategoryModel(); });
$stock_model=stage('stock_model', function(){ return new shopProductStocksModel(); });
$feature_model=stage('feature_model', function(){ return new shopFeatureModel(); });
stage('feature_rows', function() use ($feature_model){ return $feature_model->select('code,name')->fetchAll(); });
stage('all_categories', function() use ($category_model){ return $category_model->select('id,name,parent_id')->fetchAll(); });
$product_row=stage('product_row', function() use ($product_model,$product_id){ return $product_model->getById($product_id); });
$sku_row=stage('sku_row', function() use ($sku_model,$sku_id){ return $sku_model->getById($sku_id); });
stage('category_rows', function() use ($category_product_model,$product_id){
    return $category_product_model->select('category_id')->where('product_id='.(int)$product_id)->fetchAll();
});
stage('stock_row', function() use ($stock_model,$sku_id){
    return $stock_model->getByField(array('sku_id'=>$sku_id,'stock_id'=>66));
});
$product=stage('shopProduct', function() use ($product_id){ return new shopProduct($product_id); });
stage('product_features', function() use ($product){ return $product->features; });
echo "DONE\n";
PHP
chown web:web /tmp/ms_kit_stage.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_kit_stage.php'
rm -f /tmp/ms_kit_stage.php
'''
            p=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",f"root@{VM_IP}","bash -s"],
                input=remote.encode("utf-8"),capture_output=True,timeout=90)
            out=(p.stdout or b"").decode("utf-8","replace")
            if p.stderr:
                out+="\nSTDERR\n"+p.stderr.decode("utf-8","replace")
            REPORT.write_text(out,encoding="utf-8")
            print(out)
        finally:
            try:
                req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception:
                pass

if __name__=="__main__":
    main()
