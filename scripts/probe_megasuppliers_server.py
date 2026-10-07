#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile, urllib.parse, urllib.request

API_KEY=os.environ.get("NETANGELS_API_KEY","").strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=".deploy-probe/megasuppliers-server-probe.txt"

def request_json(url,method="GET",data=None,headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def run():
    os.makedirs(os.path.dirname(REPORT),exist_ok=True)
    _,tok=request_json(
        "https://panel.netangels.ru/api/gateway/token/","POST",
        urllib.parse.urlencode({"api_key":API_KEY}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"tetchair-profile-fix/1.0"},
    )
    headers={
        "Authorization":"Bearer "+tok["token"],
        "Content-Type":"application/json",
        "Accept":"application/json",
        "User-Agent":"tetchair-profile-fix/1.0",
    }
    php=r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');

$dir=wa()->getDataPath('plugins/megasuppliers/import-config',false,'shop',true);
$path=$dir.'/15.json';
if(!file_exists($path)){fwrite(STDERR,"CONFIG_NOT_FOUND\n");exit(2);}
$cfg=json_decode(file_get_contents($path),true);
if(!is_array($cfg)){fwrite(STDERR,"CONFIG_INVALID\n");exit(3);}

$m=new waModel();
$type=$m->query("SELECT id,name FROM shop_type WHERE id=52")->fetch();
$count=(int)$m->query("SELECT COUNT(*) FROM shop_product WHERE type_id=52")->fetchField();
if(!$type || (string)$type['name']!=='Тетчер' || $count<1){
    fwrite(STDERR,"TETCHAIR_TYPE_52_NOT_CONFIRMED\n");
    exit(4);
}
$wrong=$m->query("SELECT id,name FROM shop_type WHERE id=1917")->fetch();
if($wrong){
    fwrite(STDERR,"UNEXPECTED_TYPE_1917_EXISTS\n");
    exit(5);
}

$backup=$path.'.bak-tetchair-type-'.date('Ymd-His');
if(!copy($path,$backup)){fwrite(STDERR,"BACKUP_FAILED\n");exit(6);}

if(empty($cfg['webasyst'])||!is_array($cfg['webasyst']))$cfg['webasyst']=array();
if(empty($cfg['rules'])||!is_array($cfg['rules']))$cfg['rules']=array();
$cfg['webasyst']['type_id']=52;
$cfg['rules']['require_purchase_price']=true;
$cfg['rules']['price_formulas']=array(
    'purchase_price'=>'purchase_price',
    'price'=>'purchase_price * 1.25',
    'compare_price'=>'purchase_price * 1.80'
);
$cfg['mapping']['purchase_price']='purchase_price';
$cfg['mapping']['price']='price';

$json=json_encode($cfg,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
if($json===false||file_put_contents($path,$json)===false){fwrite(STDERR,"CONFIG_WRITE_FAILED\n");exit(7);}
$check=json_decode(file_get_contents($path),true);

echo json_encode(array(
    'status'=>'ok',
    'supplier_id'=>15,
    'confirmed_type'=>array('id'=>(int)$type['id'],'name'=>$type['name'],'products'=>$count),
    'type_1917_exists'=>(bool)$wrong,
    'saved_type_id'=>(int)ifset($check['webasyst']['type_id']),
    'purchase_price_field'=>(string)ifset($check['mapping']['purchase_price']),
    'require_purchase_price'=>(bool)ifset($check['rules']['require_purchase_price']),
    'formulas'=>ifset($check['rules']['price_formulas'],array()),
    'backup'=>basename($backup)
),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
?>'''
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=open(key+".pub",encoding="utf-8").read().strip()
        _,created=request_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"chatgpt-tetchair-profile-fix-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),
            headers,
        )
        key_id=created["id"]
        try:
            remote="cat >/tmp/tetchair_profile_fix.php <<'PHP'\n"+php+"\nPHP\nchown web:web /tmp/tetchair_profile_fix.php\nsu -s /bin/bash web -c 'php /tmp/tetchair_profile_fix.php'\nrc=$?\nrm -f /tmp/tetchair_profile_fix.php\nexit $rc\n"
            p=subprocess.run(
                ["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null","root@"+VM_IP,"bash -s"],
                input=remote,text=True,capture_output=True,timeout=120,
            )
            output=(p.stdout or "").strip()
            with open(REPORT,"w",encoding="utf-8") as fh:
                fh.write(output+"\n")
                if p.stderr: fh.write("stderr="+p.stderr[-1200:]+"\n")
            print(output)
            if p.stderr: print(p.stderr,file=sys.stderr)
            if p.returncode: raise RuntimeError("profile fix failed")
        finally:
            try:
                request_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/","DELETE",None,headers)
            except Exception as exc:
                print("cleanup warning",repr(exc),file=sys.stderr)

if __name__=="__main__":
    run()
