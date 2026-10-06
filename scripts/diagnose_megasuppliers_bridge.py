#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-bridge-diagnostic.txt")

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"ms-diagnose/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"ms-diagnose-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            remote=r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
echo "NOW=$(date -Is)"
echo "RECENT_LOG_FILES_BEGIN"
find "$ROOT/wa-log" /var/log -type f -mmin -180 2>/dev/null | sort | head -200
echo "RECENT_LOG_FILES_END"
echo "MEGASUPPLIERS_ERRORS_BEGIN"
for f in $(find "$ROOT/wa-log" /var/log -type f -mmin -180 2>/dev/null | sort | head -200); do
  m=$(grep -Eai 'megasuppliers|fatal error|uncaught|sqlstate|allowed memory|maximum execution|bridge' "$f" 2>/dev/null | tail -80 || true)
  if [ -n "$m" ]; then
    echo "### $f"
    printf '%s\n' "$m"
  fi
done
echo "MEGASUPPLIERS_ERRORS_END"
echo "BRIDGE_PHP_LINT_BEGIN"
php -l "$ROOT/wa-apps/shop/plugins/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendBridge.controller.php" || true
echo "BRIDGE_PHP_LINT_END"
echo "TABLE_SCHEMA_BEGIN"
cat >/tmp/ms_diag_schema.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
foreach($m->query("SHOW CREATE TABLE shop_megasuppliers_product")->fetchAll() as $r){echo array_values($r)[1],"\n";}
echo "rows=".(int)$m->query("SELECT COUNT(*) FROM shop_megasuppliers_product")->fetchField()."\n";
echo "supplier1_rows=".(int)$m->query("SELECT COUNT(*) FROM shop_megasuppliers_product WHERE supplier_id=1")->fetchField()."\n";
echo "supplier1_dups=".(int)$m->query("SELECT COUNT(*) FROM (SELECT supplier_sku FROM shop_megasuppliers_product WHERE supplier_id=1 GROUP BY supplier_sku HAVING COUNT(*)>1) x")->fetchField()."\n";
PHP
chown web:web /tmp/ms_diag_schema.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_diag_schema.php' || true
rm -f /tmp/ms_diag_schema.php
echo "TABLE_SCHEMA_END"
'''
            p=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",f"root@{VM_IP}","bash -s"],
                input=remote,text=True,capture_output=True,timeout=120)
            out=p.stdout
            if p.stderr: out+="\nSTDERR\n"+p.stderr
            REPORT.write_text(out,encoding="utf-8")
            print(out)
            if p.returncode: raise RuntimeError("remote exit "+str(p.returncode))
        finally:
            try:
                req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception as e:
                print("cleanup_warning",repr(e))

if __name__=="__main__":
    main()
