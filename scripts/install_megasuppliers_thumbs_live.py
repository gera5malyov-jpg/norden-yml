#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-thumbs-live.txt")

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, timeout=60):
    return subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=12",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"ms-thumbs/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}

    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"ms-thumbs-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            for _ in range(12):
                p=ssh(key,"echo ok",timeout=20)
                if p.returncode==0:
                    break
                import time; time.sleep(3)
            else:
                raise RuntimeError("SSH unavailable")

            files=[
                ("webasyst/megasuppliers/patches/plugin_thumb_ui.txt","/tmp/plugin_thumb_ui.txt"),
                ("webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendThumbs.controller.php","/tmp/shopMegasuppliersPluginBackendThumbs.controller.php"),
            ]
            for local,remote in files:
                subprocess.run([
                    "scp","-i",key,"-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null",
                    local,"root@"+VM_IP+":"+remote
                ],check=True,timeout=30)

            remote=r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
FILE="$PLUGIN/lib/shopMegasuppliers.plugin.php"
CFG="$PLUGIN/lib/config/plugin.php"
CTRL="$PLUGIN/lib/actions/backend/shopMegasuppliersPluginBackendThumbs.controller.php"
TS="$(date +%Y%m%d-%H%M%S)"
BACK="/home/web/backups/chatgpt-megasuppliers-thumbs/$TS"
mkdir -p "$BACK"
cp -a "$FILE" "$BACK/shopMegasuppliers.plugin.php.before"
cp -a "$CFG" "$BACK/plugin.php.before"
[ ! -f "$CTRL" ] || cp -a "$CTRL" "$BACK/thumbs.controller.php.before"
echo "BACKUP=$BACK"

cp /tmp/shopMegasuppliersPluginBackendThumbs.controller.php "$CTRL"
chown web:web "$CTRL"
chmod 644 "$CTRL"

python3 - "$FILE" "$CFG" /tmp/plugin_thumb_ui.txt <<'PY'
import re,sys
from pathlib import Path
plugin=Path(sys.argv[1]); cfg=Path(sys.argv[2]); snippet=Path(sys.argv[3]).read_text(encoding='utf-8')
s=plugin.read_text(encoding='utf-8')

def patch_method(text, marker, old_return, new_return):
    start=text.find(marker)
    if start < 0:
        raise SystemExit("missing method "+marker)
    nxt=text.find("\n    public function ", start+len(marker))
    if nxt < 0:
        nxt=text.rfind("\n}")
    part=text[start:nxt]
    if new_return.strip() in part:
        return text, False
    if old_return not in part:
        raise SystemExit("missing return in "+marker)
    part=part.replace(old_return,new_return,1)
    return text[:start]+part+text[nxt:], True

if "private function thumbnailEndpointUrl(" not in s:
    pos=s.rfind("\n}")
    if pos < 0:
        raise SystemExit("plugin class end not found")
    s=s[:pos]+"\n"+snippet+s[pos:]
    print("THUMB_HELPERS=added")
else:
    print("THUMB_HELPERS=exists")

s,changed_new=patch_method(
    s,
    "    public function backendProdList(",
    "        return array('header_left' => $html);",
    "        $html .= $this->thumbnailBootstrap();\n        return array('header_left' => $html);"
)
print("NEW_UI_BOOTSTRAP="+("added" if changed_new else "exists"))

s,changed_old=patch_method(
    s,
    "    public function backendProducts(",
    "        return array('sidebar_section' => $html);",
    "        $html .= $this->thumbnailBootstrap();\n        return array('sidebar_section' => $html);"
)
print("OLD_UI_BOOTSTRAP="+("added" if changed_old else "exists"))

plugin.write_text(s,encoding='utf-8')

c=cfg.read_text(encoding='utf-8')
c2=re.sub(r"'version'\s*=>\s*'1\.0\.8'", "'version' => '1.0.9'", c, count=1)
if c2==c:
    c2=re.sub(r"'version'\s*=>\s*'1\.0\.7'", "'version' => '1.0.9'", c, count=1)
if c2!=c:
    cfg.write_text(c2,encoding='utf-8')
    print("VERSION=1.0.9")
else:
    m=re.search(r"'version'\s*=>\s*'([^']+)'",c)
    print("VERSION="+(m.group(1) if m else "unknown"))
PY

chown web:web "$FILE" "$CFG"
chmod 644 "$FILE" "$CFG"
php -d display_errors=1 -l "$FILE"
php -d display_errors=1 -l "$CTRL"
php -d display_errors=1 -l "$CFG"

cat >/tmp/ms_thumb_verify.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$ids=array(1230239,1223347,1223332,1223333,1223374,1483283,1476554);
$rows=$m->query("SELECT id,summary,image_id FROM shop_product WHERE id IN (i:ids)",array('ids'=>$ids))->fetchAll('id');
$native=array();
foreach($m->query("SELECT DISTINCT product_id FROM shop_product_images WHERE product_id IN (i:ids)",array('ids'=>$ids))->fetchAll() as $r){$native[(int)$r['product_id']]=1;}
$found=0;
foreach($rows as $id=>$r){
  $url='';
  if(empty($r['image_id']) && empty($native[(int)$id]) && preg_match('~\[extimg\]\s*(https?://[^\s<\[]+)~iu',html_entity_decode((string)$r['summary'],ENT_QUOTES,'UTF-8'),$mm)){$url=$mm[1];}
  echo "PID=".$id." NATIVE=".(empty($native[(int)$id])?'no':'yes')." URL=".($url?substr($url,0,140):'')."\n";
  if($url)$found++;
}
echo "EXT_THUMBS_FOUND=".$found."\n";
echo "THUMB_CONTROLLER=".(class_exists('shopMegasuppliersPluginBackendThumbsController')?'yes':'no')."\n";
PHP
chown web:web /tmp/ms_thumb_verify.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_thumb_verify.php'
rm -f /tmp/ms_thumb_verify.php

rm -rf "$ROOT/wa-cache/apps/shop" "$ROOT/wa-cache/apps/system/waEvent/cache" 2>/dev/null || true
RESET="$ROOT/ms-opcache-reset-$TS.php"
cat >"$RESET" <<'PHP'
<?php
header('Content-Type: text/plain');
echo function_exists('opcache_reset') && opcache_reset() ? 'opcache_reset=yes' : 'opcache_reset=no';
PHP
chown web:web "$RESET"; chmod 644 "$RESET"
curl -kfsS --max-time 10 "https://profikompany.ru/$(basename "$RESET")" || true
echo
rm -f "$RESET"

echo "PLUGIN_THUMB_HELPERS=$(grep -c 'private function thumbnailEndpointUrl' "$FILE" || true)"
echo "PLUGIN_THUMB_BOOTSTRAPS=$(grep -c 'thumbnailBootstrap()' "$FILE" || true)"
echo "CTRL_EXISTS=$(test -f "$CTRL" && echo yes || echo no)"
curl -kfsS -o /dev/null --max-time 15 -w 'SITE_HTTP=%{http_code} SITE_TOTAL=%{time_total}\n' https://profikompany.ru/
echo "THUMB_INSTALL_STATUS=SUCCESS"
'''
            p=ssh(key,"bash -s",stdin=remote,timeout=75)
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
            except Exception as e:
                print("cleanup_warning",repr(e))

if __name__=="__main__":
    main()
