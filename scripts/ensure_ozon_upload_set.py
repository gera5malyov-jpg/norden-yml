#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse, urllib.request

VM_ID=44780
VM_IP="45.86.180.49"
ROOT="/home/web/vm-23f9aff9.na4u.ru/www"
SET_ID="ozon_upload"
SET_NAME="Грузить в Ozon"

def req_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def main():
    api_key=os.environ["NETANGELS_API_KEY"].strip()
    token=req_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST",
        urllib.parse.urlencode({"api_key":api_key}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"ensure-ozon-upload-set/1.0"},
    ).get("token")
    if not token:
        raise RuntimeError("NetAngels token missing")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json","User-Agent":"ensure-ozon-upload-set/1.0"}

    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=open(key+".pub",encoding="utf-8").read().strip()
        created=req_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",
            json.dumps({"key":pub,"name":"chatgpt-ozon-upload-set-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),
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

$set_id='ozon_upload';
$set_name='Грузить в Ozon';
$model=new shopSetModel();
$set=$model->getById($set_id);
$created=false;

if (!$set) {
    $new_id=$model->add(array('id'=>$set_id,'name'=>$set_name));
    if (!$new_id) {
        throw new Exception('Не удалось создать список '.$set_name);
    }
    $created=true;
    $set=$model->getById($set_id);
} elseif ((string)$set['name'] !== $set_name) {
    $model->update($set_id,array('name'=>$set_name));
    $set=$model->getById($set_id);
}

echo json_encode(array(
    'ok'=>true,
    'created'=>$created,
    'set_id'=>$set_id,
    'name'=>$set_name,
    'count'=>(int)$set['count'],
    'type'=>(int)$set['type']
),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
?>"""
            remote=(
                "set -eu\n"
                "cat >/tmp/ensure_ozon_upload_set.php <<'PHP'\n"+php+"\nPHP\n"
                "chown web:web /tmp/ensure_ozon_upload_set.php\n"
                "su -s /bin/bash web -c 'php /tmp/ensure_ozon_upload_set.php'\n"
                "rm -f /tmp/ensure_ozon_upload_set.php\n"
            )
            p=subprocess.run(
                ["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15","root@"+VM_IP,remote],
                text=True,capture_output=True,timeout=120,
            )
            print(p.stdout)
            if p.returncode:
                raise RuntimeError(p.stderr[-3000:] or p.stdout[-3000:])
        finally:
            try:
                req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception as e:
                print("cleanup warning:",e)

if __name__=="__main__":
    main()
