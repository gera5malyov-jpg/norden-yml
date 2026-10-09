#!/usr/bin/env python3
"""Read-only audit of the live Webasyst storefront over short-lived NetAngels SSH access.

Only public page/template names, non-secret setting *names*, and public product prices
are reported. No theme files, DB credentials or customer records leave the server.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request

VM_ID = 44780
VM_IP = "45.86.180.49"
ROOT = "/home/web/vm-23f9aff9.na4u.ru/www"

def request_json(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=35) as res:
        payload = res.read().decode("utf-8")
        return json.loads(payload) if payload else {}

REMOTE = r"""set -eu
python3 - <<'PY'
import json, os, pathlib, re
root = pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
directions = [
    root / 'wa-data/public/site/themes/pureMegapolis42',
    root / 'wa-data/public/shop/themes/pureMegapolis42',
    root / 'wa-apps/shop/themes/pureMegapolis42',
    root / 'wa-apps/site/themes/pureMegapolis42',
]
patterns = {
    'home_empty_set': re.compile('Список товаров пустой|список товаров пустой|productSet|set_id|promo-tab',re.I),
    'home_contacts': re.compile('Для звонков, гостей и писем|контакт|адрес',re.I),
    'demo_image': re.compile('pure.easyweb.su',re.I),
    'product_recs': re.compile('Рекомендуем посмотреть|related_products|cross_selling|upselling|alsoBought',re.I),
}
out = {'roots': [], 'template_matches': [], 'source_files_scanned': 0}
for d in directions:
    out['roots'].append({'path': str(d.relative_to(root)), 'exists': d.is_dir()})
    if not d.is_dir():
        continue
    for path in d.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in {'.html','.tpl','.php','.js'} or path.stat().st_size > 300000:
            continue
        out['source_files_scanned'] += 1
        try: txt = path.read_text(encoding='utf-8', errors='replace')
        except Exception: continue
        matches = {}
        for tag, pat in patterns.items():
            lines = [i+1 for i,line in enumerate(txt.splitlines()) if pat.search(line)]
            if lines: matches[tag] = lines[:10]
        if matches:
            out['template_matches'].append({'file': str(path.relative_to(root)), 'line_numbers': matches})
out['template_matches'] = out['template_matches'][:100]
print('STOREFRONT_THEME_AUDIT=' + json.dumps(out,ensure_ascii=False))
PY
p=$(mktemp /tmp/storefront-stage1-audit-XXXXXX.php)
trap 'rm -f "$p"' EXIT
cat > "$p" <<'PHP'
<?php
$root = '/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root . '/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$m = new waModel();
$out = array();
try {
  $out['sets'] = $m->query(
    "SELECT s.id, s.name, s.type, COUNT(sp.product_id) AS product_count
     FROM shop_set s LEFT JOIN shop_set_products sp ON sp.set_id=s.id
     WHERE s.id LIKE '%promo%' OR s.id LIKE '%bestsell%' OR s.id LIKE '%new%'
       OR s.id LIKE '%popular%'
     GROUP BY s.id, s.name, s.type ORDER BY s.id LIMIT 50"
  )->fetchAll();
} catch (Throwable $e) { $out['sets_error'] = get_class($e); }
try {
  $out['products'] = $m->query(
    "SELECT p.id,p.name,s.price,s.compare_price,s.count
     FROM shop_product p INNER JOIN shop_product_skus s ON s.product_id=p.id
     WHERE p.id IN (1489211,392940,763100)
     ORDER BY p.id,s.id LIMIT 12"
  )->fetchAll();
} catch (Throwable $e) { $out['products_error'] = get_class($e); }
try {
  $out['site_theme_setting_keys'] = $m->query(
    "SELECT app, name FROM wa_app_settings
     WHERE (app='site' OR app='shop') AND
           (name LIKE '%theme%' OR name LIKE '%address%' OR name LIKE '%promo%')
     ORDER BY app,name LIMIT 40"
  )->fetchAll();
} catch (Throwable $e) { $out['settings_error'] = get_class($e); }
echo 'STOREFRONT_DB_AUDIT=' . json_encode($out, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES) . "\\n";
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php $p"
"""

def run():
    api_key = os.environ.get("NETANGELS_API_KEY","").strip()
    if not api_key:
        raise RuntimeError("NETANGELS_API_KEY secret is missing")
    token = request_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST", urllib.parse.urlencode({"api_key":api_key}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"storefront-stage1-audit/1.0"}
    ).get("token")
    if not token:
        raise RuntimeError("NetAngels gateway token not issued")
    headers = {"Authorization":"Bearer "+token, "Accept":"application/json",
               "Content-Type":"application/json","User-Agent":"storefront-stage1-audit/1.0"}
    with tempfile.TemporaryDirectory(prefix="storefront-audit-") as td:
        key = os.path.join(td,"id_ed25519")
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub = open(key+".pub",encoding="utf-8").read().strip()
        result = request_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",json.dumps({"key":pub,"name":"storefront-audit-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        key_id = result.get("id")
        if not key_id:
            raise RuntimeError("Temporary SSH key was not registered")
        try:
            ssh = ["ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                   "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
                   "root@"+VM_IP,"bash -s"]
            proc = subprocess.run(ssh,input=REMOTE,text=True,capture_output=True,timeout=150)
            if proc.returncode:
                raise RuntimeError("Read-only storefront audit failed (exit {}) stderr: {}".format(
                    proc.returncode,proc.stderr[-500:]))
            for line in proc.stdout.splitlines():
                if line.startswith("STOREFRONT_"):
                    print(line,flush=True)
        finally:
            try:
                request_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/",
                             "DELETE",None,headers)
                print("temporary_ssh_key_removed=true")
            except Exception:
                print("WARNING: temporary SSH key cleanup requires review")

if __name__ == "__main__":
    run()
