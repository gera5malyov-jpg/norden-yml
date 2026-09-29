#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request, time
from pathlib import Path

API_KEY = os.environ["NETANGELS_API_KEY"].strip()
VM_ID = 44780
VM_IP = "45.86.180.49"
REPORT = Path(".deploy-probe/megasuppliers-thumbs-disabled.txt")

def req(url, method="GET", data=None, headers=None):
    q = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(q, timeout=30) as r:
        raw = r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body = urllib.parse.urlencode({"api_key": API_KEY}).encode()
    _, tok = req("https://panel.netangels.ru/api/gateway/token/", "POST", body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"ms-disable-thumbs/1.0"})
    headers = {"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}

    with tempfile.TemporaryDirectory() as td:
        key = td + "/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key], check=True)
        pub = Path(key+".pub").read_text().strip()
        _, created = req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/", "POST",
            json.dumps({"key":pub,"name":"ms-disable-thumbs-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(), headers)
        kid = created["id"]
        try:
            for _ in range(10):
                p = subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                    "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8",f"root@{VM_IP}","echo ok"],
                    text=True,capture_output=True,timeout=12)
                if p.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("SSH unavailable")

            remote = r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
FILE="$PLUGIN/lib/shopMegasuppliers.plugin.php"
CFG="$PLUGIN/lib/config/plugin.php"
CTRL="$PLUGIN/lib/actions/backend/shopMegasuppliersPluginBackendThumbs.controller.php"
TS="$(date +%Y%m%d-%H%M%S)"
BACK="/home/web/backups/chatgpt-megasuppliers-disable-thumbs/$TS"
mkdir -p "$BACK"
cp -a "$FILE" "$BACK/shopMegasuppliers.plugin.php.before"
cp -a "$CFG" "$BACK/plugin.php.before"
[ ! -f "$CTRL" ] || cp -a "$CTRL" "$BACK/thumbs.controller.php.before"
echo "BACKUP=$BACK"

python3 - "$FILE" "$CFG" <<'PY'
import re, sys
from pathlib import Path
p=Path(sys.argv[1]); cfg=Path(sys.argv[2])
s=p.read_text(encoding='utf-8')
before=s
s=s.replace("        $html .= $this->thumbnailBootstrap();\n", "")
s=s.replace("        $html.=$this->thumbnailBootstrap();\n", "")
if s != before:
    p.write_text(s,encoding='utf-8')
    print("THUMB_CALLS=removed")
else:
    print("THUMB_CALLS=already_absent")

c=cfg.read_text(encoding='utf-8')
c2=re.sub(r"'version'\s*=>\s*'1\.0\.11'", "'version' => '1.0.12'", c, count=1)
if c2 != c:
    cfg.write_text(c2,encoding='utf-8')
    print("VERSION=1.0.12")
else:
    m=re.search(r"'version'\s*=>\s*'([^']+)'",c)
    print("VERSION="+(m.group(1) if m else "unknown"))
PY

chown web:web "$FILE" "$CFG"
php -d display_errors=1 -l "$FILE"
php -d display_errors=1 -l "$CFG"

rm -rf "$ROOT/wa-cache/apps/shop" "$ROOT/wa-cache/apps/system/waEvent/cache" 2>/dev/null || true
RESET="$ROOT/ms-opcache-reset-disable-thumbs.php"
cat >"$RESET" <<'PHP'
<?php
header('Content-Type: text/plain');
echo function_exists('opcache_reset') && opcache_reset() ? 'opcache_reset=yes' : 'opcache_reset=no';
PHP
chown web:web "$RESET"; chmod 644 "$RESET"
curl -kfsS --max-time 8 "https://profikompany.ru/$(basename "$RESET")" || true
echo
rm -f "$RESET"

cat >/tmp/ms_disable_verify.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
$a=$p->backendProducts(array());
$b=$p->backendProdList(array());
$old=is_array($a)&&isset($a['sidebar_section'])?$a['sidebar_section']:'';
$new=is_array($b)&&isset($b['header_left'])?$b['header_left']:'';
echo "OLD_THUMBS=".(strpos($old,'__msExtThumbs')===false?'off':'on')."\n";
echo "NEW_THUMBS=".(strpos($new,'__msExtThumbs')===false?'off':'on')."\n";
echo "OLD_SUPPLIERS=".(strpos($old,'Поставщики')!==false?'ok':'missing')."\n";
echo "NEW_SUPPLIERS=".(strpos($new,'Поставщики')!==false?'ok':'missing')."\n";
PHP
chown web:web /tmp/ms_disable_verify.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_disable_verify.php'
rm -f /tmp/ms_disable_verify.php

echo "THUMB_CALLS_LEFT=$(grep -c 'thumbnailBootstrap();' "$FILE" || true)"
curl -kfsS -o /dev/null --max-time 12 -w 'SITE_HTTP=%{http_code} SITE_TOTAL=%{time_total}\n' https://profikompany.ru/
echo "DISABLE_STATUS=SUCCESS"
'''
            p = subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8",f"root@{VM_IP}","bash -s"],
                input=remote,text=True,capture_output=True,timeout=45)
            out = p.stdout + ("\nSTDERR\n"+p.stderr if p.stderr else "")
            REPORT.write_text(out,encoding="utf-8")
            print(out)
            if p.returncode:
                raise RuntimeError("remote exit "+str(p.returncode))
        finally:
            try:
                req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
            except Exception:
                pass

if __name__ == "__main__":
    main()
