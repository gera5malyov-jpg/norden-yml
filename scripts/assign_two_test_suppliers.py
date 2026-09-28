#!/usr/bin/env python3
import json, os, subprocess, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-test-assignments.txt")

def req_json(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r, timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=120):
    p=subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("remote failed: "+p.stdout[-1500:]+" "+p.stderr[-1500:])
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
            "POST",json.dumps({"key":pub,"name":"chatgpt-megasuppliers-two-tests"}).encode(),headers
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
cat >/tmp/ms_assign_two.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
wa('shop')->getPlugin('megasuppliers',true);

$sm=new shopMegasuppliersSupplierModel();
$now=date('Y-m-d H:i:s');
$four=$sm->getByField('code','4SIS');
if(!$four){
    $id=$sm->insert(array('name'=>'4SIS','code'=>'4SIS','active'=>1,'created_at'=>$now,'updated_at'=>$now));
    $four=$sm->getById($id);
    echo "4sis_created=yes\n";
}else{
    echo "4sis_created=no\n";
}
$norden=$sm->getByField('code','NORDEN');
if(!$norden){ throw new Exception('NORDEN supplier missing'); }

$svc=new shopMegasuppliersImportService();
$tests=array(
    array($norden,'AF-31662421'),
    array($four,'AF-31655692')
);
foreach($tests as $t){
    $supplier=$t[0]; $sku=$t[1];
    $stats=$svc->importRows((int)$supplier['id'],array(array('артикул'=>$sku)),array(
        'source'=>'manual_test',
        'filename'=>'',
        'create_missing'=>false,
        'update_catalog'=>false
    ));
    echo "assign ".$sku." supplier=".$supplier['code']." linked=".$stats['linked']." skipped=".$stats['skipped']." errors=".count($stats['errors'])."\n";
    if(!empty($stats['errors'])){ echo json_encode($stats['errors'],JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n"; }
}
$m=new waModel();
foreach(array('AF-31662421','AF-31655692') as $sku){
    $rows=$m->query(
        "SELECT p.id product_id,p.name,s.id sku_id,s.sku,sp.name supplier_name,sp.code supplier_code
         FROM shop_product_skus s
         JOIN shop_product p ON p.id=s.product_id
         LEFT JOIN shop_megasuppliers_product mp ON mp.sku_id=s.id AND mp.product_id=p.id
         LEFT JOIN shop_megasuppliers_supplier sp ON sp.id=mp.supplier_id
         WHERE s.sku=s:sku ORDER BY sp.id",
        array('sku'=>$sku)
    )->fetchAll();
    echo "verify ".$sku." ".json_encode($rows,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
PHP
chown web:web /tmp/ms_assign_two.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_assign_two.php'
rm -f /tmp/ms_assign_two.php
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=120)
            lines.append(p.stdout.strip())
            if p.stderr.strip():
                lines.append("stderr="+p.stderr.strip()[-1200:])
        finally:
            req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
    REPORT.write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
