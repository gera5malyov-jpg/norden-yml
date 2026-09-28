#!/usr/bin/env python3
import json, os, subprocess, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-ui-diagnostic.txt")

def req_json(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=90):
    p=subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(p.stdout[-2000:]+" "+p.stderr[-2000:])
    return p

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req_json("https://panel.netangels.ru/api/gateway/token/","POST",body,
                   {"Content-Type":"application/x-www-form-urlencoded"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json"}
    lines=[]
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",json.dumps({"key":pub,"name":"chatgpt-ms-ui-diag"}).encode(),headers
        )
        kid=str(created["id"])
        try:
            for _ in range(18):
                p=ssh(key,"echo ok",check=False,timeout=20)
                if p.returncode==0:
                    break
                time.sleep(3)
            remote=r'''set -e
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cat >/tmp/ms_ui_diag.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
$cfg=include $root.'/wa-apps/shop/plugins/megasuppliers/lib/config/plugin.php';
echo "VERSION=".ifset($cfg['version'])."\n";
echo "HANDLERS=".json_encode(ifset($cfg['handlers'],array()),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";

$legacy=$p->backendProducts(array());
$legacy_html=isset($legacy['sidebar_section'])?$legacy['sidebar_section']:'';
echo "LEGACY_SIDEBAR=".(strpos($legacy_html,'Поставщики')!==false?'yes':'no')."\n";
echo "LEGACY_RELOCATE_SCRIPT=".(strpos($legacy_html,'s-set-list-block')!==false?'yes':'no')."\n";

$params_list=array('products'=>array(),'products_total_count'=>0,'current_page'=>1,'pages_count'=>1);
$new=$p->backendProdList($params_list);
$new_html=isset($new['header_left'])?$new['header_left']:'';
echo "NEW_UI_HOOK=".(strpos($new_html,'s-megasuppliers-new-list')!==false?'yes':'no')."\n";
echo "NEW_UI_TARGET=".(strpos($new_html,'s-filter-categories-section')!==false?'yes':'no')."\n";

$e1=wa('shop')->event('backend_products');
$e2=wa('shop')->event('backend_prod_list',$params_list);
echo "EVENT_LEGACY=".(isset($e1['megasuppliers-plugin']['sidebar_section'])?'yes':'no')."\n";
echo "EVENT_NEW=".(isset($e2['megasuppliers-plugin']['header_left'])?'yes':'no')."\n";

$sm=new shopMegasuppliersSupplierModel();
$s4=$sm->getByField('code','4SIS');
$sn=$sm->getByField('code','NORDEN');
echo "S4=".json_encode($s4,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
echo "SN=".json_encode($sn,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";

$m=new waModel();
$pid4=(int)$m->query("SELECT p.id FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE s.sku='AF-31655692' LIMIT 1")->fetchField();
$pidn=(int)$m->query("SELECT p.id FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE s.sku='AF-31662421' LIMIT 1")->fetchField();
$pids=(int)$m->query("SELECT p.id FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE s.sku='AF-31391502' LIMIT 1")->fetchField();

if ($s4) {
    $_GET['megasupplier']=(int)$s4['id'];
    $_REQUEST['megasupplier']=(int)$s4['id'];
    $c4=new shopProductsCollection('');
    $filter_params=array('filter'=>null,'filter_options'=>array(),'collection'=>$c4);
    $p->backendProdFilters($filter_params);
    $count4=$c4->count();
    $rows4=$c4->getProducts('id',0,min(2000,max(1,$count4)),false);
    echo "FILTER_4SIS_COUNT=".$count4."\n";
    echo "FILTER_4SIS_TEST_INCLUDED=".(isset($rows4[$pid4])?'yes':'no')."\n";
    echo "FILTER_4SIS_NORDEN_EXCLUDED=".(!isset($rows4[$pidn])?'yes':'no')."\n";
}
if ($sn) {
    $_GET['megasupplier']=(int)$sn['id'];
    $_REQUEST['megasupplier']=(int)$sn['id'];
    $cn=new shopProductsCollection('');
    $filter_params_n=array('filter'=>null,'filter_options'=>array(),'collection'=>$cn);
    $p->backendProdFilters($filter_params_n);
    $countn=$cn->count();
    $rowsn=$cn->getProducts('id',0,min(2000,max(1,$countn)),false);
    echo "FILTER_NORDEN_COUNT=".$countn."\n";
    echo "FILTER_NORDEN_TEST_INCLUDED=".(isset($rowsn[$pidn])?'yes':'no')."\n";
    echo "FILTER_NORDEN_SCREENSHOT_INCLUDED=".(isset($rowsn[$pids])?'yes':'no')."\n";
    echo "FILTER_NORDEN_4SIS_EXCLUDED=".(!isset($rowsn[$pid4])?'yes':'no')."\n";
}

$product_screen=new shopProduct($pids);
$old_card=$p->backendProductEdit($product_screen);
$new_card=$p->backendProdContent(array('product'=>$product_screen,'content_id'=>'general'));
$old_card_html=isset($old_card['basics'])?$old_card['basics']:'';
$new_card_html=isset($new_card['form_bottom'])?$new_card['form_bottom']:'';
echo "CARD_SCREENSHOT_OLD_NORDEN_SELECTED=".(strpos($old_card_html,'selected>Norden</option>')!==false?'yes':'no')."\n";
echo "CARD_SCREENSHOT_NEW_NORDEN_SELECTED=".(strpos($new_card_html,'selected>Norden</option>')!==false?'yes':'no')."\n";
PHP
chown web:web /tmp/ms_ui_diag.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_ui_diag.php'
rm -f /tmp/ms_ui_diag.php
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=90)
            lines.append(p.stdout)
            if p.stderr.strip():
                lines.append("STDERR\n"+p.stderr)
        finally:
            req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
    REPORT.write_text("\n".join(lines),encoding="utf-8")

if __name__=="__main__":
    main()
