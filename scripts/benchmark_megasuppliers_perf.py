#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-perf.txt")
# rerun after optimized deploy attempt

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"ms-perf/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"ms-perf-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            remote=r'''set -e
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cat >/tmp/ms_perf.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$p=wa('shop')->getPlugin('megasuppliers',true);
$cfg=include $root.'/wa-apps/shop/plugins/megasuppliers/lib/config/plugin.php';
echo "VERSION=".ifset($cfg['version'])."\n";
echo "MAPPING_ROWS=".$m->query("SELECT COUNT(*) FROM shop_megasuppliers_product")->fetchField()."\n";
echo "MAPPED_PRODUCTS=".$m->query("SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product WHERE product_id>0")->fetchField()."\n";
$idx=array();
foreach($m->query("SHOW INDEX FROM shop_megasuppliers_product")->fetchAll() as $r){
  $idx[]=$r['Key_name'].':'.$r['Seq_in_index'].':'.$r['Column_name'];
}
echo "INDEXES=".implode(',',$idx)."\n";
$t=microtime(true);
$s=(new shopMegasuppliersSupplierModel())->select('id')->where('active=1')->fetchAll();
foreach($s as $r){
  $m->query("SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product WHERE supplier_id=i:id AND product_id>0",array('id'=>(int)$r['id']))->fetchField();
}
echo "PER_SUPPLIER_COUNTS_MS=".round((microtime(true)-$t)*1000,2)."\n";
$t=microtime(true);
$g=$m->query("SELECT supplier_id,COUNT(DISTINCT product_id) c FROM shop_megasuppliers_product WHERE product_id>0 GROUP BY supplier_id")->fetchAll();
echo "GROUP_COUNTS_MS=".round((microtime(true)-$t)*1000,2)."\n";
if(method_exists($p,'backendProdList')){
  $t=microtime(true); $x=$p->backendProdList(array());
  echo "BACKEND_PROD_LIST_MS=".round((microtime(true)-$t)*1000,2)."\n";
}
$top=$m->query("SELECT supplier_id,COUNT(DISTINCT product_id) c FROM shop_megasuppliers_product WHERE product_id>0 GROUP BY supplier_id ORDER BY c DESC LIMIT 1")->fetch();
if($top){
  $sid=(int)$top['supplier_id'];
  echo "TEST_SUPPLIER_ID=".$sid."\n";
  echo "TEST_SUPPLIER_PRODUCTS=".(int)$top['c']."\n";
  foreach(array(
    'EXISTS'=>"SELECT COUNT(*) FROM shop_product p WHERE EXISTS (SELECT 1 FROM shop_megasuppliers_product ms WHERE ms.product_id=p.id AND ms.supplier_id=".$sid.")",
    'IN'=>"SELECT COUNT(*) FROM shop_product p WHERE p.id IN (SELECT product_id FROM shop_megasuppliers_product WHERE supplier_id=".$sid." AND product_id>0)"
  ) as $n=>$q){
    $times=array();
    for($i=0;$i<3;$i++){ $t=microtime(true); $c=$m->query($q)->fetchField(); $times[]=round((microtime(true)-$t)*1000,2); }
    echo "FILTER_".$n."_COUNT=".$c."\n";
    echo "FILTER_".$n."_MS=".implode(',',$times)."\n";
  }
}
PHP
chown web:web /tmp/ms_perf.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_perf.php'
rm -f /tmp/ms_perf.php
'''
            p=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",f"root@{VM_IP}","bash -s"],
                input=remote,text=True,capture_output=True,timeout=45)
            if p.returncode:
                raise RuntimeError((p.stdout+"\n"+p.stderr)[-3000:])
            REPORT.write_text(p.stdout,encoding="utf-8")
            print(p.stdout)
        finally:
            try:
                req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception as e:
                print("cleanup_warning",repr(e))

if __name__=="__main__":
    main()
