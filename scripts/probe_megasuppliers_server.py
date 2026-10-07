#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

API_KEY=os.environ.get("NETANGELS_API_KEY","").strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=".deploy-probe/megasuppliers-server-probe.txt"

def request_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def run():
    os.makedirs(os.path.dirname(REPORT),exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=request_json(
        "https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"tetchair-type-diagnostic/1.0"}
    )
    headers={
        "Authorization":"Bearer "+tok["token"],
        "Content-Type":"application/json",
        "Accept":"application/json",
        "User-Agent":"tetchair-type-diagnostic/1.0",
    }
    php=r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$out=array();

$out['type_1917']=$m->query("SELECT id,name FROM shop_type WHERE id=1917")->fetch();
$out['type_1917_products']=(int)$m->query("SELECT COUNT(*) FROM shop_product WHERE type_id=1917")->fetchField();
$out['type_1917_skus']=(int)$m->query("SELECT COUNT(*) FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE p.type_id=1917")->fetchField();
$out['type_1917_tet_skus']=(int)$m->query("SELECT COUNT(*) FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE p.type_id=1917 AND s.sku LIKE 'tet-%'")->fetchField();
$out['type_1917_numeric_names']=(int)$m->query("SELECT COUNT(*) FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE p.type_id=1917 AND s.name REGEXP '^[0-9]+$'")->fetchField();

$out['supplier15_links']=(int)$m->query("SELECT COUNT(*) FROM shop_megasuppliers_product WHERE supplier_id=15")->fetchField();
$out['supplier15_linked_products']=(int)$m->query("SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product WHERE supplier_id=15 AND product_id>0")->fetchField();
$out['supplier15_types']=$m->query(
    "SELECT p.type_id,t.name,COUNT(DISTINCT mp.product_id) c ".
    "FROM shop_megasuppliers_product mp ".
    "JOIN shop_product p ON p.id=mp.product_id ".
    "LEFT JOIN shop_type t ON t.id=p.type_id ".
    "WHERE mp.supplier_id=15 AND mp.product_id>0 ".
    "GROUP BY p.type_id,t.name ORDER BY c DESC"
)->fetchAll();

$out['article_19248']=$m->query(
    "SELECT p.id product_id,p.type_id,p.name product_name,s.id sku_id,s.sku,s.name sku_name,s.purchase_price,s.price,s.compare_price ".
    "FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id ".
    "WHERE s.name='19248' OR s.sku='19248' OR s.sku='tet-19248' LIMIT 20"
)->fetchAll();

$out['type1917_sample']=$m->query(
    "SELECT p.id product_id,s.id sku_id,s.sku,s.name sku_name,s.purchase_price,s.price,s.compare_price ".
    "FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id ".
    "WHERE p.type_id=1917 ORDER BY p.id DESC LIMIT 20"
)->fetchAll();

$status_dir=wa()->getDataPath('plugins/megasuppliers/import-status',false,'shop',true);
$status_path=$status_dir.'/15.json';
if(file_exists($status_path)){
    $status=json_decode(file_get_contents($status_path),true);
    $out['status']=array(
        'status'=>ifset($status['status']),
        'request_id'=>ifset($status['request_id']),
        'mode'=>ifset($status['mode']),
        'report_plan'=>ifset($status['report']['plan'],array()),
        'report_errors'=>ifset($status['report']['errors'],array()),
    );
}
echo json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
?>'''
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=open(key+".pub",encoding="utf-8").read().strip()
        _,created=request_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"chatgpt-tetchair-type-diag-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),
            headers
        )
        key_id=created["id"]
        try:
            remote="cat >/tmp/tetchair_type_diag.php <<'PHP'\n"+php+"\nPHP\nchown web:web /tmp/tetchair_type_diag.php\nsu -s /bin/bash web -c 'php /tmp/tetchair_type_diag.php'\nrc=$?\nrm -f /tmp/tetchair_type_diag.php\nexit $rc\n"
            p=subprocess.run(
                ["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null","root@"+VM_IP,"bash -s"],
                input=remote,text=True,capture_output=True,timeout=120
            )
            output=(p.stdout or "").strip()
            open(REPORT,"w",encoding="utf-8").write(output+"\n")
            print(output)
            if p.stderr: print(p.stderr,file=sys.stderr)
            if p.returncode: raise RuntimeError("diagnostic failed")
        finally:
            try:
                request_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/","DELETE",None,headers)
            except Exception as exc:
                print("cleanup warning",repr(exc),file=sys.stderr)

if __name__=="__main__":
    run()
