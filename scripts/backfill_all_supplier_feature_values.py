#!/usr/bin/env python3
import json, os, subprocess, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-feature681-backfill.txt")

def req_json(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=240):
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
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json"}

    # Wait until VM operations such as scheduled backup have finished.
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
            "POST",
            json.dumps({"key":pub,"name":"chatgpt-megasuppliers-feature681-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),
            headers
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
cat >/tmp/ms_feature681_backfill.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
wa('shop')->getPlugin('megasuppliers',true);

$m=new waModel();
$sm=new shopMegasuppliersSupplierModel();
$values=$m->query(
    "SELECT fv.id,fv.value,COUNT(DISTINCT pf.product_id) total
     FROM shop_product_features pf
     JOIN shop_feature_values_varchar fv ON fv.id=pf.feature_value_id
     WHERE pf.feature_id=681
     GROUP BY fv.id,fv.value
     HAVING total>0
     ORDER BY fv.value"
)->fetchAll();

$created=0; $linked=0; $processed=0;
foreach($values as $v){
    $vid=(int)$v['id'];
    $name=trim((string)$v['value']);
    if($name===''){ continue; }

    $unmapped=(int)$m->query(
        "SELECT COUNT(DISTINCT pf.product_id)
         FROM shop_product_features pf
         WHERE pf.feature_id=681 AND pf.feature_value_id=i:vid
           AND NOT EXISTS (
             SELECT 1 FROM shop_megasuppliers_product mp
             WHERE mp.product_id=pf.product_id AND mp.product_id>0
           )",
        array('vid'=>$vid)
    )->fetchField();
    if(!$unmapped){ continue; }

    $supplier=$m->query(
        "SELECT * FROM shop_megasuppliers_supplier
         WHERE LOWER(TRIM(name))=LOWER(TRIM(s:name)) LIMIT 1",
        array('name'=>$name)
    )->fetch();
    if(!$supplier){
        $code='LEGACY681_'.$vid;
        $supplier=$sm->getByField('code',$code);
        if(!$supplier){
            $now=date('Y-m-d H:i:s');
            $sid=$sm->insert(array(
                'name'=>$name,'code'=>$code,'active'=>1,
                'created_at'=>$now,'updated_at'=>$now
            ));
            $supplier=$sm->getById($sid);
            $created++;
        }
    }

    $sid=(int)$supplier['id'];
    $before=(int)$m->query(
        "SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product
         WHERE supplier_id=i:sid AND product_id>0",array('sid'=>$sid)
    )->fetchField();

    $m->exec(
        "INSERT IGNORE INTO shop_megasuppliers_product
         (supplier_id,product_id,sku_id,supplier_sku,external_id,purchase_price,stock,raw_json,updated_at)
         SELECT i:sid,p.id,s.id,s.sku,'',NULL,NULL,NULL,NOW()
         FROM shop_product_features pf
         JOIN shop_product p ON p.id=pf.product_id
         JOIN shop_product_skus s ON s.id=p.sku_id
         WHERE pf.feature_id=681 AND pf.feature_value_id=i:vid
           AND TRIM(COALESCE(s.sku,''))<>''
           AND NOT EXISTS (
             SELECT 1 FROM shop_megasuppliers_product mp
             WHERE mp.product_id=p.id AND mp.product_id>0
           )",
        array('sid'=>$sid,'vid'=>$vid)
    );

    $after=(int)$m->query(
        "SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product
         WHERE supplier_id=i:sid AND product_id>0",array('sid'=>$sid)
    )->fetchField();
    $added=max(0,$after-$before);
    $linked += $added;
    $processed++;
    echo "value=".$vid." supplier=".$supplier['code']." name=".$name." added=".$added."\n";
}
echo "created_suppliers=".$created."\n";
echo "linked_products=".$linked."\n";
echo "processed_values=".$processed."\n";
echo "unmapped_with_supplier_feature=".(int)$m->query(
    "SELECT COUNT(DISTINCT pf.product_id)
     FROM shop_product_features pf
     WHERE pf.feature_id=681
       AND NOT EXISTS (
         SELECT 1 FROM shop_megasuppliers_product mp
         WHERE mp.product_id=pf.product_id AND mp.product_id>0
       )"
)->fetchField()."\n";
PHP
chown web:web /tmp/ms_feature681_backfill.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_feature681_backfill.php'
rm -f /tmp/ms_feature681_backfill.php
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=240)
            out.append(p.stdout.strip())
            if p.stderr.strip():
                out.append("STDERR\n"+p.stderr.strip()[-1500:])
        finally:
            try:
                req_json(f"https://api-ms.netangels.ru/api/v1/sshkeys/{kid}/","DELETE",None,headers)
            except Exception:
                pass

    REPORT.write_text("\n".join(out)+"\n",encoding="utf-8")
    print("\n".join(out))

if __name__=="__main__":
    main()
