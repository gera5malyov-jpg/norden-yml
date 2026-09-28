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
