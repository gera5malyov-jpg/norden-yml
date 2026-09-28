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

def ssh(key, command, stdin=None, check=True, timeout=90):
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
$r=$p->backendProducts(array());
echo "DIRECT_SIDEBAR=".(isset($r['sidebar_section'])?'yes':'no')." LEN=".strlen(ifset($r['sidebar_section'],''))."\n";
$e=wa('shop')->event('backend_products');
echo "EVENT_KEYS=".implode(',',array_keys($e))."\n";
echo "EVENT_MS_SIDEBAR=".(isset($e['megasuppliers']['sidebar_section'])?'yes':'no')."\n";
$sm=new shopMegasuppliersSupplierModel();
$s=$sm->getByField('code','4SIS');
echo "S4=".json_encode($s,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
if ($s) {
    $_GET['megasupplier']=(int)$s['id'];
    $_REQUEST['megasupplier']=(int)$s['id'];
    $c=new shopProductsCollection('');
    echo "COUNT_BEFORE=".$c->count()."\n";
    $params=array('filter'=>null,'filter_options'=>array(),'collection'=>$c);
    $p->backendProdFilters($params);
    echo "COUNT_AFTER=".$c->count()."\n";
    $m=new waModel();
    $pid=$m->query("SELECT p.id FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE s.sku='AF-31655692' LIMIT 1")->fetchField();
    $one=$c->getProducts('id',0,5000,false);
    echo "TEST_PID=".$pid." INCLUDED=".(isset($one[$pid])?'yes':'no')."\n";
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
