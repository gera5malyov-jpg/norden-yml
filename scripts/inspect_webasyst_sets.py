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
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"inspect-webasyst-sets/1.0"},
    ).get("token")
    if not token:
        raise RuntimeError("NetAngels token missing")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json","User-Agent":"inspect-webasyst-sets/1.0"}

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
echo json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
?>"""
            remote=(
                "set -eu\n"
                "cat >/tmp/inspect_webasyst_sets.php <<'PHP'\n"+php+"\nPHP\n"
                "chown web:web /tmp/inspect_webasyst_sets.php\n"
                "su -s /bin/bash web -c 'php /tmp/inspect_webasyst_sets.php'\n"
                "rm -f /tmp/inspect_webasyst_sets.php\n"
            )
            p=subprocess.run(
                ["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15","root@"+VM_IP,remote],
                text=True,capture_output=True,timeout=120,
            )
            if p.returncode:
                raise RuntimeError(p.stderr[-3000:] or p.stdout[-3000:])
            print(p.stdout)
        finally:
            try:
                request_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception as e:
                print("cleanup warning:",e)

if __name__=="__main__":
    main()
