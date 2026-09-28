#!/usr/bin/env python3
import json, os, subprocess, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-final-check.txt")

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
        raise RuntimeError(p.stdout[-3000:]+" "+p.stderr[-3000:])
    return p

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req_json("https://panel.netangels.ru/api/gateway/token/","POST",body,
                   {"Content-Type":"application/x-www-form-urlencoded"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}

    deadline=time.time()+1200
    while True:
        _,vms=req_json("https://api-ms.netangels.ru/api/v1/cloud/vms/?limit=100","GET",None,headers)
        vm=next((x for x in vms.get("entities",[]) if int(x.get("id",0))==VM_ID),None)
        state=str((vm or {}).get("state",""))
        print("vm_state="+state,flush=True)
        if state=="Active":
            break
        if state in ("Stopped","StoppedByAdmin","StoppedByService","Error"):
            raise RuntimeError("VM not active: "+state)
        if time.time()>=deadline:
            raise RuntimeError("VM wait timeout: "+state)
        time.sleep(15)

    out=[]
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",json.dumps({"key":pub,"name":"chatgpt-ms-final-check-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers
        )
        kid=int(created["id"])
        try:
            for _ in range(20):
                p=ssh(key,"echo ok",check=False,timeout=20)
                if p.returncode==0:
                    break
                time.sleep(3)
            else:
                raise RuntimeError("SSH unavailable")

            remote=r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cat >/tmp/ms_final_check.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
$cfg=include $root.'/wa-apps/shop/plugins/megasuppliers/lib/config/plugin.php';
echo "VERSION=".ifset($cfg['version'])."\n";

$legacy=$p->backendProducts(array());
$legacy_html=isset($legacy['sidebar_section'])?$legacy['sidebar_section']:'';
echo "OLD_UI_SUPPLIERS=".(strpos($legacy_html,'Поставщики')!==false?'yes':'no')."\n";
echo "OLD_UI_RELOCATE=".(strpos($legacy_html,'s-set-list-block')!==false?'yes':'no')."\n";

$params_list=array('products'=>array(),'products_total_count'=>0,'current_page'=>1,'pages_count'=>1);
$new=$p->backendProdList($params_list);
$new_html=isset($new['header_left'])?$new['header_left']:'';
echo "NEW_UI_SUPPLIERS=".(strpos($new_html,'s-megasuppliers-new-list')!==false?'yes':'no')."\n";
echo "NEW_UI_FILTER_TARGET=".(strpos($new_html,'s-filter-categories-section')!==false?'yes':'no')."\n";

$events_new=wa('shop')->event('backend_prod_list',$params_list);
echo "NEW_UI_EVENT=".(isset($events_new['megasuppliers-plugin']['header_left'])?'yes':'no')."\n";

$sm=new shopMegasuppliersSupplierModel();
$m=new waModel();
$tests=array(
    '4SIS'=>array('AF-31655692','AF-31662421'),
    'NORDEN'=>array('AF-31662421','AF-31655692')
);
foreach($tests as $code=>$skus){
    $s=$sm->getByField('code',$code);
    if(!$s){ echo "FILTER_".$code."_SUPPLIER=no\n"; continue; }
    $_GET['megasupplier']=(int)$s['id'];
    $_REQUEST['megasupplier']=(int)$s['id'];
    $c=new shopProductsCollection('');
    $params=array('filter'=>null,'filter_options'=>array(),'collection'=>$c);
    $p->backendProdFilters($params);
    try {
        $count=$c->count();
        $wanted=(int)$m->query("SELECT product_id FROM shop_product_skus WHERE sku=s:sku LIMIT 1",array('sku'=>$skus[0]))->fetchField();
        $other=(int)$m->query("SELECT product_id FROM shop_product_skus WHERE sku=s:sku LIMIT 1",array('sku'=>$skus[1]))->fetchField();
        $check=new shopProductsCollection('');
        $_GET['megasupplier']=(int)$s['id'];
        $_REQUEST['megasupplier']=(int)$s['id'];
        $params2=array('filter'=>null,'filter_options'=>array(),'collection'=>$check);
        $p->backendProdFilters($params2);
        $rows=$check->getProducts('id',0,3000,false);
        echo "FILTER_".$code."_EXEC=yes COUNT=".$count."\n";
        echo "FILTER_".$code."_TARGET=".(isset($rows[$wanted])?'yes':'no')."\n";
        echo "FILTER_".$code."_OTHER_EXCLUDED=".(!isset($rows[$other])?'yes':'no')."\n";
    } catch(Throwable $e) {
        echo "FILTER_".$code."_EXEC=no ".get_class($e).":".$e->getMessage()."\n";
    }
}

foreach(array('AF-31391502'=>'NORDEN','AF-31662421'=>'NORDEN','AF-31655692'=>'4SIS') as $sku=>$code){
    $row=$m->query(
        "SELECT sp.code,p.id product_id
         FROM shop_product_skus s
         JOIN shop_product p ON p.id=s.product_id
         LEFT JOIN shop_megasuppliers_product mp ON mp.product_id=p.id
         LEFT JOIN shop_megasuppliers_supplier sp ON sp.id=mp.supplier_id
         WHERE s.sku=s:sku
         ORDER BY mp.updated_at DESC,mp.id DESC LIMIT 1",
        array('sku'=>$sku)
    )->fetch();
    $mapped=($row && $row['code']===$code);
    echo "MAP_".$sku."=".($mapped?'yes':'no')."\n";
    if($row && $row['product_id']){
        $product=new shopProduct((int)$row['product_id']);
        $old=$p->backendProductEdit($product);
        $newcard=$p->backendProdContent(array('product'=>$product,'content_id'=>'general'));
        $oldhtml=isset($old['basics'])?$old['basics']:'';
        $newhtml=isset($newcard['form_bottom'])?$newcard['form_bottom']:'';
        echo "CARD_".$sku."_OLD_SELECTED=".(strpos($oldhtml,'selected>'.$code) !== false || strpos($oldhtml,'selected>Norden') !== false || strpos($oldhtml,'selected>4SIS') !== false ? 'yes':'no')."\n";
        echo "CARD_".$sku."_NEW_SELECTED=".(strpos($newhtml,'selected>'.$code) !== false || strpos($newhtml,'selected>Norden') !== false || strpos($newhtml,'selected>4SIS') !== false ? 'yes':'no')."\n";
    }
}

$unmapped=(int)$m->query(
    "SELECT COUNT(DISTINCT pf.product_id)
     FROM shop_product_features pf
     WHERE pf.feature_id=681
       AND NOT EXISTS (
         SELECT 1 FROM shop_megasuppliers_product mp
         WHERE mp.product_id=pf.product_id AND mp.product_id>0
       )"
)->fetchField();
echo "UNMAPPED_WITH_SUPPLIER_FEATURE=".$unmapped."\n";
PHP
chown web:web /tmp/ms_final_check.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_final_check.php'
rm -f /tmp/ms_final_check.php
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=150)
            out.append(p.stdout.strip())
            if p.stderr.strip():
                out.append("STDERR\n"+p.stderr.strip()[-1600:])
        finally:
            try:
                req_json(f"https://api-ms.netangels.ru/api/v1/sshkeys/{kid}/","DELETE",None,headers)
            except Exception:
                pass

    REPORT.write_text("\n".join(out)+"\n",encoding="utf-8")
    print("\n".join(out))

if __name__=="__main__":
    main()
