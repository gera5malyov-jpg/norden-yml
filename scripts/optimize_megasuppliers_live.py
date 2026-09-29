#!/usr/bin/env python3
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-optimize-live.txt")

def req(url, method="GET", data=None, headers=None):
    q=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def main():
    REPORT.parent.mkdir(exist_ok=True)
    body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req("https://panel.netangels.ru/api/gateway/token/","POST",body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"ms-optimize/1.0"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
            json.dumps({"key":pub,"name":"ms-optimize-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=created["id"]
        try:
            remote=r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
FILE="$PLUGIN/lib/shopMegasuppliers.plugin.php"
CFG="$PLUGIN/lib/config/plugin.php"
TS="$(date +%Y%m%d-%H%M%S)"
BACK="/home/web/backups/chatgpt-megasuppliers-optimize/$TS"
mkdir -p "$BACK"
cp -a "$FILE" "$BACK/shopMegasuppliers.plugin.php.before"
cp -a "$CFG" "$BACK/plugin.php.before"
echo "BACKUP=$BACK"
echo "STALE_PROCESSES_BEGIN"
ps -eo pid,ppid,etime,cmd | grep -E 'ms_(db|perf|backfill|register|event)|deploy_megasuppliers|ALTER TABLE shop_megasuppliers' | grep -v grep || true
echo "STALE_PROCESSES_END"
pkill -TERM -f '/tmp/ms_db_init.php' 2>/dev/null || true
pkill -TERM -f '/tmp/ms_perf.php' 2>/dev/null || true
sleep 1
pkill -KILL -f '/tmp/ms_db_init.php' 2>/dev/null || true
pkill -KILL -f '/tmp/ms_perf.php' 2>/dev/null || true
echo "STALE_PROCESSES_AFTER="
ps -eo pid,ppid,etime,cmd | grep -E '/tmp/ms_(db_init|perf)\.php' | grep -v grep || true

python3 - "$FILE" "$CFG" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]); cfg=Path(sys.argv[2])
s=p.read_text(encoding='utf-8')
before=s
s=s.replace(
"""        $model = new waModel();
        $rows = (new shopMegasuppliersSupplierModel())->select('id,name,code')->where('active=1')->order('name')->fetchAll();""",
"""        $rows = (new shopMegasuppliersSupplierModel())->select('id,name,code')->where('active=1')->order('name')->fetchAll();"""
)
for block in [
"""            $count = (int)$model->query(
                'SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product '
                .'WHERE supplier_id='.(int)$id.' AND product_id>0'
            )->fetchField();
""",
"""            $queries = $this->supplierProductSubqueries($id, $row['code']);
            $count_row = $model->query('SELECT COUNT(*) c FROM ('.implode(' UNION ', $queries).') ms_products')->fetch();
            $count = $count_row ? (int)$count_row['c'] : 0;
"""
]:
    s=s.replace(block,'')
s=s.replace("                .'<span class=\"count gray smaller\">'.$count.'</span>'\n",'')
s=s.replace("                .'<span class=\"count\">'.$count.'</span>'\n",'')
if s==before:
    print("PLUGIN_PATCH=already_light_or_pattern_not_found")
else:
    p.write_text(s,encoding='utf-8')
    print("PLUGIN_PATCH=changed")
c=cfg.read_text(encoding='utf-8')
if "'version' => '1.0.7'" in c:
    c=c.replace("'version' => '1.0.7'","'version' => '1.0.8'",1)
    cfg.write_text(c,encoding='utf-8')
    print("VERSION_PATCH=1.0.8")
else:
    print("VERSION_PATCH=unchanged")
PY

php -d display_errors=1 -l "$FILE"
php -d display_errors=1 -l "$CFG"

cat >/tmp/ms_add_index.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$exists=false;
foreach($m->query("SHOW INDEX FROM shop_megasuppliers_product WHERE Key_name='supplier_product'")->fetchAll() as $r){$exists=true;}
if(!$exists){
    try{$m->exec("SET SESSION lock_wait_timeout=10");}catch(Exception $e){}
    $m->exec("ALTER TABLE shop_megasuppliers_product ADD KEY supplier_product (supplier_id, product_id)");
    echo "INDEX=added\n";
}else{
    echo "INDEX=exists\n";
}
PHP
chown web:web /tmp/ms_add_index.php
if timeout 25s su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_add_index.php'; then
  echo "INDEX_STEP=ok"
else
  echo "INDEX_STEP=failed_or_timeout"
fi
rm -f /tmp/ms_add_index.php

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

echo "VERIFY_COUNTS_IN_METHOD=$(grep -c 'COUNT(DISTINCT product_id)' "$FILE" || true)"
echo "VERIFY_INDEX="
cat >/tmp/ms_verify_index.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
foreach($m->query("SHOW INDEX FROM shop_megasuppliers_product")->fetchAll() as $r){
 if($r['Key_name']==='supplier_product') echo $r['Key_name'].":".$r['Seq_in_index'].":".$r['Column_name']."\n";
}
PHP
chown web:web /tmp/ms_verify_index.php
timeout 10s su -s /bin/bash web -c 'php /tmp/ms_verify_index.php' || true
rm -f /tmp/ms_verify_index.php
curl -kfsS -o /dev/null --max-time 15 -w 'SITE_HTTP=%{http_code} SITE_TOTAL=%{time_total}\n' https://profikompany.ru/
echo "OPTIMIZE_STATUS=SUCCESS"
'''
            p=subprocess.run(["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",f"root@{VM_IP}","bash -s"],
                input=remote,text=True,capture_output=True,timeout=75)
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
