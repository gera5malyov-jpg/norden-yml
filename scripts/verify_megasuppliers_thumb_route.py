#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-thumb-route-verify.txt")

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"ms-route-verify/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"ms-route-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            remote=r'''set -e
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
rm -rf "$ROOT/wa-cache/apps/shop" "$ROOT/wa-cache/apps/system/waEvent/cache" 2>/dev/null || true

cat >/tmp/ms_route.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
$cfg=include $root.'/wa-apps/shop/plugins/megasuppliers/lib/config/plugin.php';
echo "VERSION=".ifset($cfg['version'])."\n";
echo "INTERACTION_URL=".$p->getInteractionUrl('thumbs')."\n";
$a=$p->backendProducts(array());
$b=$p->backendProdList(array());
echo "OLD_HOOK=".(is_array($a)&&isset($a['sidebar_section'])&&strpos($a['sidebar_section'],'__msExtThumbs')!==false?'ok':'bad')."\n";
echo "NEW_HOOK=".(is_array($b)&&isset($b['header_left'])&&strpos($b['header_left'],'__msExtThumbs')!==false?'ok':'bad')."\n";
PHP
chown web:web /tmp/ms_route.php
OUT="$(su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_route.php')"
echo "$OUT"
URL="$(printf '%s\n' "$OUT" | sed -n 's/^INTERACTION_URL=//p' | tail -1)"
rm -f /tmp/ms_route.php
[ -n "$URL" ] || { echo ROUTE_MISSING; exit 4; }

case "$URL" in
  http://*|https://*) TEST_URL="$URL" ;;
  /*) TEST_URL="https://profikompany.ru$URL" ;;
  *) TEST_URL="https://profikompany.ru/$URL" ;;
esac
HTTP="$(curl -k -sS -o /tmp/ms_route_body --max-time 12 -w '%{http_code}' "$TEST_URL" || true)"
echo "ROUTE_HTTP=$HTTP"
if [ "$HTTP" = 404 ] || [ "$HTTP" = 000 ]; then
  echo "ROUTE_BAD"
  head -c 500 /tmp/ms_route_body 2>/dev/null || true
  exit 5
fi
echo "ROUTE_OK"
rm -f /tmp/ms_route_body

RESET="$ROOT/ms-opcache-reset-route.php"
cat >"$RESET" <<'PHP'
<?php
header('Content-Type: text/plain');
echo function_exists('opcache_reset') && opcache_reset() ? 'opcache_reset=yes' : 'opcache_reset=no';
PHP
chown web:web "$RESET"; chmod 644 "$RESET"
curl -kfsS --max-time 8 "https://profikompany.ru/$(basename "$RESET")" || true
echo
rm -f "$RESET"

curl -kfsS -o /dev/null --max-time 12 -w 'SITE_HTTP=%{http_code} SITE_TOTAL=%{time_total}\n' https://profikompany.ru/
echo "VERIFY_STATUS=SUCCESS"
'''
            p=subprocess.run([
                "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=12",
                f"root@{VM_IP}","bash -s"
            ],input=remote,text=True,capture_output=True,timeout=40)
            out=p.stdout
            if p.stderr:
                out+="\nSTDERR\n"+p.stderr
            REPORT.write_text(out,encoding="utf-8")
            print(out)
            if p.returncode:
                raise RuntimeError("remote exit "+str(p.returncode))
        finally:
            try:
                req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception:
                pass

if __name__=="__main__":
    main()
