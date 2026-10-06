#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse, urllib.request

VM_ID=44780
VM_IP="45.86.180.49"
ROOT="/home/web/vm-23f9aff9.na4u.ru/www"

def request_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def main():
    api_key=os.environ.get("NETANGELS_API_KEY","").strip()
    if not api_key:
        raise RuntimeError("NETANGELS_API_KEY is not set")
    token=request_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST",
        urllib.parse.urlencode({"api_key":api_key}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"inspect-webasyst-sets/1.1"},
    ).get("token")
    if not token:
        raise RuntimeError("NetAngels token missing")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json","User-Agent":"inspect-webasyst-sets/1.1"}

    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=open(key+".pub",encoding="utf-8").read().strip()
        created=request_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",
            json.dumps({"key":pub,"name":"chatgpt-inspect-sets-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),
            headers,
        )
        kid=created.get("id")
        if not kid:
            raise RuntimeError("temporary SSH key id missing")
        try:
            php=r"""<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');

$m=new waModel();
$out=array();

$out['shop_set_columns']=$m->query("SHOW COLUMNS FROM shop_set")->fetchAll();
$out['sets']=$m->query("SELECT * FROM shop_set ORDER BY id LIMIT 200")->fetchAll();
$out['shop_set_products_columns']=$m->query("SHOW COLUMNS FROM shop_set_products")->fetchAll();

$out['upload_set']=$m->query("SELECT * FROM shop_set WHERE id='ozon_upload' OR name='Грузить в Ozon' ORDER BY id")->fetchAll();
$out['upload_products']=$m->query("
    SELECT p.id,p.name,p.status,p.type_id,p.category_id,
           s.id AS sku_id,s.sku,s.name AS sku_name,s.available,s.status AS sku_status,s.count
    FROM shop_set_products sp
    INNER JOIN shop_product p ON p.id=sp.product_id
    LEFT JOIN shop_product_skus s ON s.product_id=p.id
    WHERE sp.set_id='ozon_upload'
    ORDER BY sp.sort,p.id,s.id
    LIMIT 50
")->fetchAll();

$out['product_columns']=$m->query("SHOW COLUMNS FROM shop_product")->fetchAll();
$out['sku_columns']=$m->query("SHOW COLUMNS FROM shop_product_skus")->fetchAll();
$out['image_columns']=$m->query("SHOW COLUMNS FROM shop_product_images")->fetchAll();

$out['ozon_features']=$m->query("
    SELECT id,parent_id,code,name,type,multiple,status
    FROM shop_feature
    WHERE LOWER(code) LIKE '%ozon%'
       OR LOWER(name) LIKE '%ozon%'
       OR LOWER(code) LIKE '%market%'
       OR LOWER(name) LIKE '%маркет%'
       OR LOWER(code) LIKE '%kit%'
       OR LOWER(name) LIKE '%kit%'
    ORDER BY id
")->fetchAll();

$out['category_type_features']=$m->query("
    SELECT id,parent_id,code,name,type,multiple,status
    FROM shop_feature
    WHERE LOWER(code) LIKE '%categor%'
       OR LOWER(name) LIKE '%категор%'
       OR LOWER(code) LIKE '%type%'
       OR LOWER(name) LIKE '%тип%'
    ORDER BY id
    LIMIT 500
")->fetchAll();

$out['installed_shop_plugins']=array();
$plugin_root=$root.'/wa-apps/shop/plugins';
if(is_dir($plugin_root)){
    foreach(scandir($plugin_root) as $d){
        if($d==='.' || $d==='..' || !is_dir($plugin_root.'/'.$d)) continue;
        $out['installed_shop_plugins'][]=$d;
    }
    sort($out['installed_shop_plugins']);
}

$out['wa_app_settings_columns']=$m->query("SHOW COLUMNS FROM wa_app_settings")->fetchAll();

$out['legacy_ozon_samples']=$m->query("
    SELECT sp.set_id,p.id AS product_id,p.name,p.type_id,p.category_id,
           s.id AS sku_id,s.sku,s.name AS sku_name,s.purchase_price,s.price,s.compare_price
    FROM shop_set_products sp
    INNER JOIN shop_product p ON p.id=sp.product_id
    INNER JOIN shop_product_skus s ON s.product_id=p.id
    WHERE sp.set_id IN ('shchyashcht_norden','ever','ozon','ozon-lo','ozon_td_andrey','ozon_natur','ozon_merdes','lover-zerkala')
      AND s.sku IS NOT NULL AND s.sku<>''
    ORDER BY sp.set_id,sp.sort,p.id,s.id
    LIMIT 40
")->fetchAll();
$legacy_ids=array();
foreach($out['legacy_ozon_samples'] as $r){$legacy_ids[(int)$r['product_id']]=(int)$r['product_id'];}
if($legacy_ids){
    $pf_cols=array();
    foreach($m->query("SHOW COLUMNS FROM shop_product_features")->fetchAll() as $col){
        if(isset($col['Field'])) $pf_cols[$col['Field']]=true;
    }
    $out['product_feature_columns']=array_keys($pf_cols);
    $select=array('pf.product_id','pf.feature_id','f.code','f.name','f.type');
    foreach(array('sku_id','feature_value_id','value_id','value_int','value_double','value_decimal','value_varchar','value_text') as $col){
        if(isset($pf_cols[$col])) $select[]='pf.'.$col;
    }
    $out['legacy_feature_rows']=$m->query("
        SELECT ".implode(',',$select)."
        FROM shop_product_features pf
        INNER JOIN shop_feature f ON f.id=pf.feature_id
        WHERE pf.product_id IN (i:ids)
        ORDER BY pf.product_id,pf.feature_id
        LIMIT 5000
    ",array('ids'=>array_values($legacy_ids)))->fetchAll();
}else{$out['legacy_feature_rows']=array();$out['product_feature_columns']=array();}

$out['likely_card_features']=$m->query("
    SELECT id,parent_id,code,name,type,multiple,status
    FROM shop_feature
    WHERE LOWER(code) IN (
      'tip_ozon','brand','manufacturer','weight','weight_net','weight_gross',
      'width','height','depth','length','color','material','country',
      'barcode','gtin','ean','description_category_id','ozon_type_id',
      'type_ozon','ozon_category_id','ozon_product_id','ozon_sku'
    )
    ORDER BY id
")->fetchAll();

$product_ids=array();
foreach($out['upload_products'] as $r){ $product_ids[(int)$r['id']]=(int)$r['id']; }
if($product_ids){
    $out['upload_feature_rows']=$m->query("
        SELECT pf.product_id,pf.sku_id,pf.feature_id,f.code,f.name,f.type,
               pf.feature_value_id,pf.value_int,pf.value_double,pf.value_decimal,pf.value_varchar,pf.value_text
        FROM shop_product_features pf
        INNER JOIN shop_feature f ON f.id=pf.feature_id
        WHERE pf.product_id IN (i:ids)
        ORDER BY pf.product_id,pf.feature_id
        LIMIT 2000
    ",array('ids'=>array_values($product_ids)))->fetchAll();

    $out['upload_images']=$m->query("
        SELECT *
        FROM shop_product_images
        WHERE product_id IN (i:ids)
        ORDER BY product_id,sort,id
        LIMIT 500
    ",array('ids'=>array_values($product_ids)))->fetchAll();
}else{
    $out['upload_feature_rows']=array();
    $out['upload_images']=array();
}

try {
    $p=wa('shop')->getPlugin('ozonstocksync',true);
    $info=$p->getInfo();
    $out['plugin']=array(
        'version'=>isset($info['version'])?$info['version']:'',
        'update_stocks'=>(string)$p->getSettings('update_stocks'),
        'update_prices'=>(string)$p->getSettings('update_prices'),
        'dry_run'=>(string)$p->getSettings('dry_run'),
        'tip_feature_code'=>(string)$p->getSettings('tip_feature_code'),
        'accounts'=>array(),
    );
    for($i=1;$i<=10;$i++){
        $suffix=$i===1?'':'_'.$i;
        $cid=trim((string)$p->getSettings('client_id'.$suffix));
        $key=trim((string)$p->getSettings('api_key'.$suffix));
        $maps=trim((string)$p->getSettings('mappings'.$suffix));
        $name=trim((string)$p->getSettings('account_name'.$suffix));
        if($cid!=='' || $key!=='' || $maps!==''){
            $out['plugin']['accounts'][]=array(
                'index'=>$i,
                'name'=>$name,
                'has_client_id'=>$cid!=='' ? 1:0,
                'has_api_key'=>$key!=='' ? 1:0,
                'mappings'=>$maps,
            );
        }
    }
} catch(Exception $e) {
    $out['plugin_error']=$e->getMessage();
}

echo json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
?>"""
            remote=(
                "set -eu\n"
                "cat >/tmp/inspect_webasyst_sets.php <<'PHP'\n"+php+"\nPHP\n"
                "chown web:web /tmp/inspect_webasyst_sets.php\n"
                "su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/inspect_webasyst_sets.php'\n"
                "rm -f /tmp/inspect_webasyst_sets.php\n"
            )
            p=subprocess.run(
                ["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15","root@"+VM_IP,remote],
                text=True,capture_output=True,timeout=180,
            )
            if p.returncode:
                raise RuntimeError((p.stdout[-12000:] + "\nSTDERR:\n" + p.stderr[-6000:]).strip())
            print(p.stdout)
        finally:
            try:
                request_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception as e:
                print("cleanup warning:",e)

if __name__=="__main__":
    main()
