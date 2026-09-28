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
    out=[]
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
                           "POST",json.dumps({"key":pub,"name":"chatgpt-ms-final-check"}).encode(),headers)
        kid=str(created["id"])
        try:
            for _ in range(18):
                p=ssh(key,"echo ok",check=False,timeout=20)
                if p.returncode==0: break
                time.sleep(3)
            remote=r'''set -e
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cat >/tmp/ms_final_check.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
$sm=new shopMegasuppliersSupplierModel();

foreach(array('4SIS','NORDEN') as $code){
    $s=$sm->getByField('code',$code);
    if(!$s){ echo "FILTER_".$code."_SUPPLIER=no\n"; continue; }
    $_GET['megasupplier']=(int)$s['id'];
    $_REQUEST['megasupplier']=(int)$s['id'];
    $c=new shopProductsCollection('');
    $params=array('filter'=>null,'filter_options'=>array(),'collection'=>$c);
    $p->backendProdFilters($params);
    $sql=$c->getSQL();
    echo "FILTER_".$code."_SQL_ALIAS=".(strpos($sql,'p.id IN')!==false?'yes':'no')."\n";
    try {
        $rows=$c->getProducts('id',0,1,false);
        echo "FILTER_".$code."_EXEC=yes ROWS=".count($rows)."\n";
    } catch (Throwable $e) {
        echo "FILTER_".$code."_EXEC=no ".get_class($e).":".$e->getMessage()."\n";
    }
}

$m=new waModel();
foreach(array('AF-31391502'=>'NORDEN','AF-31662421'=>'NORDEN','AF-31655692'=>'4SIS') as $sku=>$code){
    $row=$m->query(
        "SELECT sp.code,p.id product_id FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id LEFT JOIN shop_megasuppliers_product mp ON mp.product_id=p.id LEFT JOIN shop_megasuppliers_supplier sp ON sp.id=mp.supplier_id WHERE s.sku=s:sku ORDER BY mp.updated_at DESC,mp.id DESC LIMIT 1",
        array('sku'=>$sku)
    )->fetch();
    echo "MAP_".$sku."=".(($row && $row['code']===$code)?'yes':'no')."\n";
}
PHP
chown web:web /tmp/ms_final_check.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_final_check.php'
rm -f /tmp/ms_final_check.php
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=60)
            out.append(p.stdout.strip())
            if p.stderr.strip(): out.append("STDERR\n"+p.stderr.strip()[-1200:])
        finally:
            req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
    REPORT.write_text("\n".join(out)+"\n",encoding="utf-8")
    print("\n".join(out))

if __name__=="__main__":
    main()
