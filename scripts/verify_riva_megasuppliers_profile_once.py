#!/usr/bin/env python3
"""One-time Riva profile safety correction and read-only import verification."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

VM_ID = 44780
VM_IP = "45.86.180.49"
REPO_ROOT = Path(__file__).resolve().parents[1]


def api(url, headers=None, data=None, method="GET"):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


def read_and_fix_profile():
    key = os.environ["NETANGELS_API_KEY"].strip()
    token = api(
        "https://panel.netangels.ru/api/gateway/token/",
        {"Content-Type": "application/x-www-form-urlencoded"},
        urllib.parse.urlencode({"api_key": key}).encode(), "POST",
    )["token"]
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"}
    with tempfile.TemporaryDirectory() as tmp:
        ssh_key = str(Path(tmp) / "id_riva")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", ssh_key], check=True)
        pub = Path(ssh_key + ".pub").read_text().strip()
        created = api(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            headers, json.dumps({"key": pub, "name": "chatgpt-riva-profile-audit-" + os.environ.get("GITHUB_RUN_ID", "once")}).encode(), "POST",
        )
        key_id = created["id"]
        try:
            php = r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$rows=(new shopMegasuppliersSupplierModel())->select('id,code,name')->fetchAll();
$matches=array();
foreach ($rows as $row) {
    if (strtoupper(trim((string)$row['code']))==='RIVA') $matches[]=$row;
}
if (count($matches)!==1) {fwrite(STDERR,"RIVA_SUPPLIER_NOT_UNIQUE\n");exit(3);}
$supplier=$matches[0];
$dir=wa()->getDataPath('plugins/megasuppliers/import-config',false,'shop',true);
$path=$dir.'/'.(int)$supplier['id'].'.json';
if (!is_file($path)) {fwrite(STDERR,"RIVA_CONFIG_NOT_FOUND\n");exit(4);}
$raw=file_get_contents($path);
$cfg=json_decode($raw,true);
if (!is_array($cfg)) {fwrite(STDERR,"RIVA_CONFIG_INVALID\n");exit(5);}
$host=parse_url((string)ifset($cfg['source']['location']), PHP_URL_HOST);
if ($host!=='riva.ru') {fwrite(STDERR,"RIVA_SOURCE_HOST_NOT_CONFIRMED\n");exit(6);}
$warehouses=ifset($cfg['rules']['warehouses'],array());
if (!is_array($warehouses) || count($warehouses)<2) {fwrite(STDERR,"RIVA_WAREHOUSE_MAPPING_INCOMPLETE\n");exit(7);}
$changed=false;
if (!empty($cfg['rules']['export_to_kit'])) {
    $cfg['rules']['export_to_kit']=false; $changed=true;
}
$images=ifset($cfg['mapping']['images'],array());
if ($images===array('picture')) {
    $cfg['mapping']['images']=array('pictures'); $changed=true;
}
if (!empty($cfg['rules']['zero_other_stocks'])) {
    $cfg['rules']['zero_other_stocks']=false; $changed=true;
}
if ($changed) {
    $backup=$path.'.backup-'.date('Ymd-His');
    if (!copy($path,$backup)) {fwrite(STDERR,"BACKUP_FAILED\n");exit(8);}
    $json=json_encode($cfg,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
    if ($json===false || waFiles::write($path,$json)===false) {fwrite(STDERR,"SAVE_FAILED\n");exit(9);}
}
echo json_encode($cfg,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
?>'''
            cmd = (
                "cat >/tmp/chatgpt_riva_safety.php <<'PHP'\n"
                + php + "\nPHP\n"
                "chown web:web /tmp/chatgpt_riva_safety.php\n"
                "su -s /bin/bash web -c 'php -d display_errors=0 /tmp/chatgpt_riva_safety.php'\n"
                "ret=$?; rm -f /tmp/chatgpt_riva_safety.php; exit $ret"
            )
            attempts = []
            for attempt in range(4):
                p = subprocess.run(
                    ["ssh", "-i", ssh_key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                     "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=20", "root@" + VM_IP, "bash -s"],
                    input=cmd, capture_output=True, text=True, timeout=100,
                )
                if p.returncode == 0:
                    cfg = json.loads(p.stdout)
                    print("riva_profile_valid=yes")
                    print("riva_mapped_warehouses=" + str(len(cfg["rules"]["warehouses"])))
                    print("riva_kit_export_enabled=" + str(bool(cfg["rules"].get("export_to_kit"))).lower())
                    print("riva_images_field=" + ",".join(cfg["mapping"].get("images") or []))
                    return cfg
                attempts.append(p.returncode)
                if "Permission denied" not in p.stderr or attempt >= 3:
                    raise RuntimeError("Riva profile read/update failed (returncode=" + str(p.returncode) + ")")
                time.sleep(5)
            raise RuntimeError("ssh attempts failed: " + str(attempts))
        finally:
            try:
                api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/", headers, method="DELETE")
                print("temporary_ssh_key_removed=yes")
            except Exception:
                print("WARNING: temporary SSH key cleanup requires verification")


def main():
    cfg = read_and_fix_profile()
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "riva.json"
        output_path = Path(tmp) / "result.json"
        config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        process = subprocess.run(
            [sys.executable, "-m", "supplier_engine.runner", str(config_path),
             "--mode", "dry-run", "--output", str(output_path)],
            cwd=REPO_ROOT, text=True, capture_output=True, timeout=2400,
        )
        if not output_path.is_file():
            raise RuntimeError("Supplier dry-run exited without JSON report: " + str(process.returncode))
        report = json.loads(output_path.read_text(encoding="utf-8"))
        print("riva_dry_run_status=" + str(report.get("status")))
        print("riva_dry_run_source_rows=" + str(report.get("source_rows")))
        print("riva_dry_run_valid_products=" + str(report.get("products")))
        source = report.get("source") or {}
        print("riva_dry_run_skipped=" + str(source.get("skipped_rows", 0)))
        print("riva_dry_run_skip_reasons=" + json.dumps(source.get("skipped_reasons") or {}, ensure_ascii=False))
        print("riva_dry_run_plan=" + json.dumps(report.get("plan") or {}, ensure_ascii=False)[:2500])
        for item in (report.get("warnings") or [])[:6]:
            print("warning=" + str(item)[:450])
        for item in (report.get("errors") or [])[:10]:
            print("error=" + str(item)[:650])
        if process.returncode != 0:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
