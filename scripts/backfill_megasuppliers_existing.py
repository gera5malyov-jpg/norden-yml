#!/usr/bin/env python3
import json, os, subprocess, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-backfill.txt")

def req_json(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=180):
    p=subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("remote failed: "+p.stdout[-2500:]+" "+p.stderr[-2500:])
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
            "POST",json.dumps({"key":pub,"name":"chatgpt-megasuppliers-backfill"}).encode(),headers
        )
        kid=str(created["id"])
        try:
            for _ in range(18):
                p=ssh(key,"echo ok",check=False,timeout=25)
                if p.returncode==0:
                    break
                time.sleep(4)
            else:
                raise RuntimeError("ssh unavailable")

            remote=r'''set -euo pipefail
cat >/tmp/ms_backfill.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
wa('shop')->getPlugin('megasuppliers',true);

$model=new waModel();
$supplier_model=new shopMegasuppliersSupplierModel();

$feature_aliases=array(
    'NORDEN'=>array('norden'),
    '4SIS'=>array('4sis'),
    'AFINA'=>array('afinalux'),
    'ALETAN'=>array('aletan.ru','aletan-provence.ru'),
    'DEEPHOUSE'=>array('deephouse'),
    'LEVMAR'=>array('левмар'),
    'TREEZ'=>array('treez'),
    'RED_BLACK'=>array('red-black'),
    'TD_ANDREY'=>array('тд андрей','андрей'),
    'TD_NIKITIN'=>array('кмк тд никитин'),
    '4SEASONS'=>array('4 сезона','4seasons'),
    'YOURROOM'=>array('yourroom'),
    'KENNER'=>array('kenner'),
    'B2B_FABRIKA'=>array('b2b fabrika','в2в')
);

$type_map=array(
    'NORDEN'=>array(142),
    'TD_ANDREY'=>array(126),
    'DEEPHOUSE'=>array(24),
    'LEVMAR'=>array(125),
    'TREEZ'=>array(199),
    'YOURROOM'=>array(26),
    'AFINA'=>array(63),
    'ALETAN'=>array(46,122,202),
    'RED_BLACK'=>array(150),
    'B2B_FABRIKA'=>array(204),
    'KENNER'=>array(203),
    '4SIS'=>array(11)
);

function qstr($s) {
    return "'" . str_replace("'", "''", $s) . "'";
}
function supplier_count($model,$sid) {
    return (int)$model->query(
        "SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product WHERE supplier_id=i:sid AND product_id>0",
        array('sid'=>(int)$sid)
    )->fetchField();
}
function insert_from_where($model,$sid,$where) {
    $sql="INSERT IGNORE INTO shop_megasuppliers_product
        (supplier_id,product_id,sku_id,supplier_sku,external_id,purchase_price,stock,raw_json,updated_at)
        SELECT ".(int)$sid.",p.id,s.id,s.sku,'',NULL,NULL,NULL,NOW()
        FROM shop_product p
        JOIN shop_product_skus s ON s.id=p.sku_id
        WHERE TRIM(COALESCE(s.sku,''))<>''
          AND NOT EXISTS (
            SELECT 1 FROM shop_megasuppliers_product mp WHERE mp.product_id=p.id AND mp.product_id>0
          )
          AND (".$where.")";
    $model->exec($sql);
}

echo "phase=feature_aliases\n";
foreach($feature_aliases as $code=>$aliases) {
    $supplier=$supplier_model->getByField('code',$code);
    if(!$supplier){ echo "missing_supplier=".$code."\n"; continue; }
    $before=supplier_count($model,$supplier['id']);
    $quoted=array();
    foreach($aliases as $v){ $quoted[]=qstr(mb_strtolower(trim($v),'UTF-8')); }
    $where="EXISTS (
        SELECT 1
        FROM shop_product_features pf
        JOIN shop_feature_values_varchar fv ON fv.id=pf.feature_value_id
        WHERE pf.product_id=p.id
          AND pf.feature_id=681
          AND LOWER(TRIM(fv.value)) IN (".implode(',',$quoted).")
    )";
    insert_from_where($model,$supplier['id'],$where);
    $after=supplier_count($model,$supplier['id']);
    echo "feature ".$code." before=".$before." after=".$after." added=".($after-$before)."\n";
}

echo "phase=type_fallback\n";
foreach($type_map as $code=>$type_ids) {
    $supplier=$supplier_model->getByField('code',$code);
    if(!$supplier){ echo "missing_supplier=".$code."\n"; continue; }
    $before=supplier_count($model,$supplier['id']);
    $ids=array_map('intval',$type_ids);
    insert_from_where($model,$supplier['id'],"p.type_id IN (".implode(',',$ids).")");
    $after=supplier_count($model,$supplier['id']);
    echo "type ".$code." before=".$before." after=".$after." added=".($after-$before)."\n";
}

echo "verify_samples\n";
foreach(array('AF-31391502','AF-31662421','AF-31655692') as $sku) {
    $rows=$model->query(
        "SELECT p.id product_id,p.type_id,s.sku,sp.name supplier_name,sp.code supplier_code
         FROM shop_product_skus s
         JOIN shop_product p ON p.id=s.product_id
         LEFT JOIN shop_megasuppliers_product mp ON mp.product_id=p.id
         LEFT JOIN shop_megasuppliers_supplier sp ON sp.id=mp.supplier_id
         WHERE s.sku=s:sku
         ORDER BY mp.updated_at DESC, mp.id DESC",
        array('sku'=>$sku)
    )->fetchAll();
    echo $sku."=".json_encode($rows,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}

echo "summary\n";
$rows=$model->query(
    "SELECT sp.code,sp.name,COUNT(DISTINCT mp.product_id) c
     FROM shop_megasuppliers_supplier sp
     LEFT JOIN shop_megasuppliers_product mp ON mp.supplier_id=sp.id AND mp.product_id>0
     GROUP BY sp.id,sp.code,sp.name
     ORDER BY sp.name"
)->fetchAll();
foreach($rows as $r){ echo $r['code']."\t".$r['name']."\t".$r['c']."\n"; }
PHP
chown web:web /tmp/ms_backfill.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_backfill.php'
rm -f /tmp/ms_backfill.php
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=180)
            lines.append(p.stdout.strip())
            if p.stderr.strip():
                lines.append("STDERR\n"+p.stderr.strip()[-2500:])
        finally:
            req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
    REPORT.write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
