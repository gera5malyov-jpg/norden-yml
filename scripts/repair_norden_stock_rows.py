#!/usr/bin/env python3
import json, os, subprocess, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/norden-stock-repair.txt")

def req_json(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=120):
    p=subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(p.stdout[-4000:]+" "+p.stderr[-4000:])
    return p

def main():
    REPORT.parent.mkdir(exist_ok=True)
    token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req_json("https://panel.netangels.ru/api/gateway/token/","POST",token_body,
                   {"Content-Type":"application/x-www-form-urlencoded"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json"}
    lines=[]
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",json.dumps({"key":pub,"name":"chatgpt-norden-stock-repair"}).encode(),headers
        )
        kid=str(created["id"])
        try:
            for _ in range(18):
                p=ssh(key,"echo ok",check=False,timeout=20)
                if p.returncode==0:
                    break
                time.sleep(3)
            remote=r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cat >/tmp/ms_repair_norden_stocks.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$cutoff='2026-10-04 20:00:00';
$type_id=142;
$main_stock=66;
$expected_contact=20317;

$groups=$m->query(
    "SELECT IFNULL(contact_id,0) contact_id,COUNT(*) c
     FROM shop_product
     WHERE type_id=i:type_id AND create_datetime>=s:cutoff
     GROUP BY contact_id",
    array('type_id'=>$type_id,'cutoff'=>$cutoff)
)->fetchAll();
echo 'CONTACT_GROUPS='.json_encode($groups,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if (count($groups)!==1 || (int)$groups[0]['contact_id']!==$expected_contact) {
    fwrite(STDERR,"Refusing repair: target set contains unexpected creator/contact\n");
    exit(3);
}

$product_count=(int)$m->query(
    "SELECT COUNT(*) FROM shop_product WHERE type_id=i:type_id AND create_datetime>=s:cutoff AND contact_id=i:contact",
    array('type_id'=>$type_id,'cutoff'=>$cutoff,'contact'=>$expected_contact)
)->fetchField();
$sku_count=(int)$m->query(
    "SELECT COUNT(*)
     FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id
     WHERE p.type_id=i:type_id AND p.create_datetime>=s:cutoff AND p.contact_id=i:contact",
    array('type_id'=>$type_id,'cutoff'=>$cutoff,'contact'=>$expected_contact)
)->fetchField();
$stock_count=(int)$m->query("SELECT COUNT(*) FROM shop_stock")->fetchField();
echo "TARGET_PRODUCTS={$product_count}\n";
echo "TARGET_SKUS={$sku_count}\n";
echo "STOCKS={$stock_count}\n";
if ($product_count < 1 || $sku_count < 1 || $stock_count < 2) {
    fwrite(STDERR,"Refusing repair: unexpected target counts\n");
    exit(4);
}

$m->exec("START TRANSACTION");
try {
    $m->exec(
        "DELETE ps FROM shop_product_stocks ps
         JOIN shop_product p ON p.id=ps.product_id
         WHERE p.type_id=i:type_id AND p.create_datetime>=s:cutoff
           AND p.contact_id=i:contact AND ps.stock_id<>i:main_stock",
        array('type_id'=>$type_id,'cutoff'=>$cutoff,'contact'=>$expected_contact,'main_stock'=>$main_stock)
    );
    $m->exec(
        "INSERT INTO shop_product_stocks (sku_id,stock_id,product_id,count)
         SELECT s.id,st.id,p.id,0
         FROM shop_product p
         JOIN shop_product_skus s ON s.product_id=p.id
         CROSS JOIN shop_stock st
         WHERE p.type_id=i:type_id AND p.create_datetime>=s:cutoff
           AND p.contact_id=i:contact AND st.id<>i:main_stock",
        array('type_id'=>$type_id,'cutoff'=>$cutoff,'contact'=>$expected_contact,'main_stock'=>$main_stock)
    );
    $m->exec("COMMIT");
} catch (Exception $e) {
    $m->exec("ROLLBACK");
    throw $e;
}

$zero_rows=(int)$m->query(
    "SELECT COUNT(*)
     FROM shop_product_stocks ps
     JOIN shop_product p ON p.id=ps.product_id
     WHERE p.type_id=i:type_id AND p.create_datetime>=s:cutoff
       AND p.contact_id=i:contact AND ps.stock_id<>i:main_stock AND ps.count=0",
    array('type_id'=>$type_id,'cutoff'=>$cutoff,'contact'=>$expected_contact,'main_stock'=>$main_stock)
)->fetchField();
$expected_zero=$sku_count*($stock_count-1);
$main_rows=(int)$m->query(
    "SELECT COUNT(*)
     FROM shop_product_stocks ps
     JOIN shop_product p ON p.id=ps.product_id
     WHERE p.type_id=i:type_id AND p.create_datetime>=s:cutoff
       AND p.contact_id=i:contact AND ps.stock_id=i:main_stock",
    array('type_id'=>$type_id,'cutoff'=>$cutoff,'contact'=>$expected_contact,'main_stock'=>$main_stock)
)->fetchField();
echo "ZERO_ROWS={$zero_rows}\n";
echo "EXPECTED_ZERO_ROWS={$expected_zero}\n";
echo "MAIN_ROWS={$main_rows}\n";
if ($zero_rows!==$expected_zero) {
    fwrite(STDERR,"Verification failed: explicit zero rows mismatch\n");
    exit(5);
}
echo "REPAIR_STATUS=SUCCESS\n";
PHP
chown web:web /tmp/ms_repair_norden_stocks.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_repair_norden_stocks.php'
rm -f /tmp/ms_repair_norden_stocks.php
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=180)
            lines.append(p.stdout)
            if p.stderr.strip():
                lines.append("STDERR\n"+p.stderr)
        finally:
            req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
    REPORT.write_text("\n".join(lines),encoding="utf-8")

if __name__=="__main__":
    main()
