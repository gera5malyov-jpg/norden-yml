#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/kit-manifest-json.txt")

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"kit-json/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"kit-json-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            remote=r'''set -euo pipefail
cat >/tmp/ms_kit_json.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');

function vals($value) {
    $out=array();
    $append=function($item) use (&$out,&$append) {
        if (is_array($item)) { foreach($item as $part){ $append($part); } return; }
        if (is_object($item)) {
            if (method_exists($item,'__toString')) { $item=(string)$item; }
            elseif (isset($item->value)) { $item=$item->value; }
            else { return; }
        }
        if (is_bool($item)) $item=$item?'Да':'Нет';
        if (is_scalar($item)) {
            $text=trim(strip_tags((string)$item));
            if ($text!=='' && !in_array($text,$out,true)) $out[]=$text;
        }
    };
    $append($value);
    return $out;
}

$link_model=new shopMegasuppliersProductModel();
$links=$link_model->query("SELECT supplier_sku,product_id,sku_id,purchase_price,stock FROM shop_megasuppliers_product WHERE supplier_id=1 ORDER BY id LIMIT 200 OFFSET 0")->fetchAll();
$product_model=new shopProductModel();
$sku_model=new shopProductSkusModel();
$category_product_model=new shopCategoryProductsModel();
$category_model=new shopCategoryModel();
$stock_model=new shopProductStocksModel();
$feature_model=new shopFeatureModel();

$feature_names=array();
foreach($feature_model->select('code,name')->fetchAll() as $fr){
    $code=trim((string)$fr['code']);
    if($code!=='') $feature_names[$code]=(string)$fr['name'];
}
$category_map=array();
foreach($category_model->select('id,name,parent_id')->fetchAll() as $c){
    $cid=(int)$c['id'];
    if($cid) $category_map[$cid]=array('id'=>$cid,'name'=>(string)$c['name'],'parent_id'=>(int)$c['parent_id']);
}

$items=array(); $used=array(); $idx=0;
foreach($links as $link){
    $idx++;
    $product_id=(int)$link['product_id']; $sku_id=(int)$link['sku_id'];
    try {
        $product_row=$product_model->getById($product_id);
        $sku_row=$sku_model->getById($sku_id);
        if(!$product_row || !$sku_row || (int)$sku_row['product_id']!==$product_id) continue;
        $category_rows=$category_product_model->select('category_id')->where('product_id='.(int)$product_id)->fetchAll();
        $category_ids=array();
        foreach($category_rows as $cr){ $cid=(int)$cr['category_id']; if($cid){$category_ids[]=$cid;$used[$cid]=true;} }
        $stock_row=$stock_model->getByField(array('sku_id'=>$sku_id,'stock_id'=>66));
        $stock=$stock_row && isset($stock_row['count'])?(float)$stock_row['count']:0.0;
        $product=new shopProduct($product_id);
        $raw=$product->features; $features=array();
        if(is_array($raw)){
            foreach($raw as $code=>$value){
                $code=(string)$code;
                $values=vals($value);
                if($values) $features[]=array('code'=>$code,'name'=>isset($feature_names[$code])?$feature_names[$code]:$code,'values'=>$values);
            }
        }
        $summary=(string)$product_row['summary'];
        $image_urls=array();
        foreach(preg_split('/\\R+/',$summary) as $line){
            $url=trim((string)$line);
            if($url!=='' && preg_match('~^https?://~i',$url) && !in_array($url,$image_urls,true)) $image_urls[]=$url;
        }
        $item=array(
            'supplier_sku'=>(string)$link['supplier_sku'],
            'product_id'=>$product_id,'sku_id'=>$sku_id,
            'sku'=>(string)$sku_row['sku'],'sku_name'=>(string)$sku_row['name'],
            'name'=>(string)$product_row['name'],'description'=>(string)$product_row['description'],
            'summary'=>$summary,'status'=>(int)$product_row['status'],
            'purchase_price'=>isset($sku_row['purchase_price'])?(float)$sku_row['purchase_price']:null,
            'stock'=>$stock,'category_ids'=>$category_ids,'features'=>$features,'image_urls'=>$image_urls,
        );
        $j=json_encode($item,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        if($j===false){
            echo "ITEM_JSON_FAIL index=$idx product_id=$product_id sku_id=$sku_id supplier_sku=".(string)$link['supplier_sku']." error=".json_last_error_msg()."\n";
            foreach($item as $k=>$v){
                if(json_encode($v,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)===false){
                    echo "FIELD_FAIL=$k error=".json_last_error_msg()."\n";
                }
            }
            exit(3);
        }
        $items[]=$item;
    } catch(Throwable $e) {
        echo "ITEM_EXCEPTION index=$idx product_id=$product_id sku_id=$sku_id supplier_sku=".(string)$link['supplier_sku']." ".get_class($e).": ".$e->getMessage()."\n";
        exit(2);
    }
}
$response=array('status'=>'ok','items'=>$items,'categories'=>array_values($category_map),'offset'=>0,'limit'=>200);
$j=json_encode($response,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
echo "ITEMS=".count($items)." RESPONSE_JSON=".($j===false?'FAIL':'OK')." ERROR=".json_last_error_msg()." BYTES=".($j===false?0:strlen($j))."\n";
PHP
chown web:web /tmp/ms_kit_json.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_kit_json.php'
rm -f /tmp/ms_kit_json.php
'''
            p=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",f"root@{VM_IP}","bash -s"],
                input=remote.encode("utf-8"),capture_output=True,timeout=120)
            out=(p.stdout or b"").decode("utf-8","replace")
            if p.stderr:
                out+="\nSTDERR\n"+p.stderr.decode("utf-8","replace")
            REPORT.write_text(out,encoding="utf-8")
            print(out)
        finally:
            try: req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception: pass

if __name__=="__main__":
    main()
