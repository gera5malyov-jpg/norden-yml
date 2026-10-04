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

def ssh(key, command, stdin=None, check=True, timeout=60):
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

$m=new waModel();
$checks=array(
    'AF-31391502'=>'NORDEN',
    'AF-31662421'=>'NORDEN',
    'AF-31655692'=>'4SIS'
);
foreach($checks as $sku=>$expected){
    $row=$m->query(
        "SELECT p.id product_id,p.type_id,s.sku,sp.name supplier_name,sp.code supplier_code
         FROM shop_product_skus s
         JOIN shop_product p ON p.id=s.product_id
         LEFT JOIN shop_megasuppliers_product mp ON mp.product_id=p.id
         LEFT JOIN shop_megasuppliers_supplier sp ON sp.id=mp.supplier_id
         WHERE s.sku=s:sku
         ORDER BY mp.updated_at DESC,mp.id DESC LIMIT 1",
        array('sku'=>$sku)
    )->fetch();
    echo "MAP_".$sku."=".json_encode($row,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
    echo "MAP_".$sku."_OK=".($row && $row['supplier_code']===$expected?'yes':'no')."\n";
    if($row){
        $product=new shopProduct((int)$row['product_id']);
        $old_card=$p->backendProductEdit($product);
        $new_card=$p->backendProdContent(array('product'=>$product,'content_id'=>'general'));
        $old_html=isset($old_card['basics'])?$old_card['basics']:'';
        $new_html=isset($new_card['form_bottom'])?$new_card['form_bottom']:'';
        $needle='selected>'.htmlspecialchars($row['supplier_name'],ENT_QUOTES,'UTF-8').'</option>';
        echo "CARD_".$sku."_OLD_SELECTED=".(strpos($old_html,$needle)!==false?'yes':'no')."\n";
        echo "CARD_".$sku."_NEW_SELECTED=".(strpos($new_html,$needle)!==false?'yes':'no')."\n";
    }
}
PHP
chown web:web /tmp/ms_ui_diag.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_ui_diag.php'
rm -f /tmp/ms_ui_diag.php

# Focused read-only diagnostic for a newly imported Norden product whose editor returns HTTP 500.
TARGET_PRODUCT_ID=1484506
LOG_DIR="$ROOT/wa-log"
echo "NORDEN_PRODUCT_DIAG_ID=$TARGET_PRODUCT_ID"
cat >/tmp/ms_norden_product_diag.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$id=1484506;
$m=new waModel();
$tables=array(
  "PRODUCT"=>"SELECT * FROM shop_product WHERE id=".(int)$id,
  "SKUS"=>"SELECT * FROM shop_product_skus WHERE product_id=".(int)$id,
  "LINK"=>"SELECT * FROM shop_megasuppliers_product WHERE product_id=".(int)$id
);
foreach($tables as $label=>$sql){
  try {
    $rows=$m->query($sql)->fetchAll();
    echo $label."=".json_encode($rows,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
  } catch(Exception $e) {
    echo $label."_ERROR=".$e->getMessage()."\n";
  }
}
try {
  $p=new shopProduct($id);
  echo "SHOP_PRODUCT_CONSTRUCT=yes\n";
  echo "SHOP_PRODUCT=".json_encode($p->getData(),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
} catch(Throwable $e) {
  echo "SHOP_PRODUCT_CONSTRUCT_ERROR=".get_class($e).": ".$e->getMessage()." @ ".$e->getFile().":".$e->getLine()."\n";
}
try {
  $plugin=wa('shop')->getPlugin('megasuppliers',true);
  $p=new shopProduct($id);
  $x=$plugin->backendProductEdit($p);
  echo "PLUGIN_BACKEND_PRODUCT_EDIT=yes\n";
} catch(Throwable $e) {
  echo "PLUGIN_BACKEND_PRODUCT_EDIT_ERROR=".get_class($e).": ".$e->getMessage()." @ ".$e->getFile().":".$e->getLine()."\n";
}
PHP
chown web:web /tmp/ms_norden_product_diag.php
su -s /bin/bash web -c 'php -d display_errors=0 -d log_errors=0 /tmp/ms_norden_product_diag.php' || true
rm -f /tmp/ms_norden_product_diag.php


echo "NORDEN_SOURCE_TARGET_BEGIN"
python3 - <<'PY'
import urllib.request, xml.etree.ElementTree as ET
url='https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.xml'
target='PR.243.WH.O59.BL'
req=urllib.request.Request(url,headers={'User-Agent':'Megasuppliers-Diagnostic/1.0'})
with urllib.request.urlopen(req,timeout=120) as r:
    data=r.read()
root=ET.fromstring(data)
for item in root.iter('Номенклатура'):
    if (item.findtext('Артикул') or '').strip()==target:
        for child in list(item):
            tag=(child.tag or '').strip()
            val=(child.text or '').strip()
            if val:
                print(f'{tag}={val}')
        break
else:
    print('TARGET_NOT_FOUND')
PY
echo "NORDEN_SOURCE_TARGET_END"
echo "NORDEN_STOCK_DIAG_BEGIN"
cat >/tmp/ms_norden_stock_diag.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$id=1484506;
$sku_id=(int)$m->query("SELECT id FROM shop_product_skus WHERE product_id=".(int)$id." LIMIT 1")->fetchField();
$stocks=$m->query("SELECT id,name,sort,public FROM shop_stock ORDER BY sort,id")->fetchAll();
echo "STOCKS=".json_encode($stocks,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
$rows=$m->query("SELECT * FROM shop_product_stocks WHERE product_id=".(int)$id." OR sku_id=".(int)$sku_id." ORDER BY stock_id")->fetchAll();
echo "PRODUCT_STOCK_ROWS=".json_encode($rows,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
echo "NORDEN_NEW_COUNT=".$m->query("SELECT COUNT(*) FROM shop_product WHERE type_id=142 AND create_datetime>='2026-10-04 20:00:00'")->fetchField()."\n";
PHP
chown web:web /tmp/ms_norden_stock_diag.php
su -s /bin/bash web -c 'php -d display_errors=0 -d log_errors=0 /tmp/ms_norden_stock_diag.php' || true
rm -f /tmp/ms_norden_stock_diag.php
echo "NORDEN_STOCK_DIAG_END"
echo "RECENT_LOG_MATCHES_BEGIN"
grep -R -n -E '1484506|shopProduct|product.*1484506|megasuppliers' "$LOG_DIR" 2>/dev/null | tail -120 || true
echo "RECENT_LOG_MATCHES_END"
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
