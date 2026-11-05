#!/usr/bin/env python3
"""One-time, auditable Ozon Stock Sync check after API key rotation.

Uses the existing NetAngels management credential, creates a temporary SSH
key, verifies the scheduled protection worker and reads server-side logs.
It does not submit stock or price updates.
"""
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.parse
import urllib.request

HOST = "45.86.180.49"
ROOT = "/home/web/vm-23f9aff9.na4u.ru/www"
VM = 44780
REPORT = pathlib.Path(".deploy-probe/ozs-key-rotation-check-20261105.txt")

def request(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=35) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def main():
    key_secret = os.environ["NETANGELS_API_KEY"].strip()
    token = request("https://panel.netangels.ru/api/gateway/token/", "POST",
                    urllib.parse.urlencode({"api_key": key_secret}).encode(),
                    {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "ozs-key-check/1.0"})["token"]
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    with tempfile.TemporaryDirectory() as tmp:
        private = tmp + "/id"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", private], check=True)
        pub = pathlib.Path(private + ".pub").read_text().strip()
        key_id = request(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM}/ssh/create/",
                         "POST", json.dumps({"key": pub, "name": "ozs-status-20261105"}).encode(), headers)["id"]
        try:
            remote = r'''set -u
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/ozonstocksync"
echo '===SERVER_TIME==='
date -Is
echo '===PLUGIN_VERSION==='
grep -E "'version'" "$PLUGIN/lib/config/plugin.php" || true
echo '===CRON_TIMERS_AND_STOCK_PRICES==='
crontab -u web -l 2>/dev/null | grep -Ei 'ozonstocksync|action_timer_worker' || true
echo '===TIMER_WORKER_PHP==='
php -l "$PLUGIN/lib/cli/action_timer_worker.php"
php -l "$PLUGIN/lib/cli/shopOzonstocksyncPluginActionTimer.cli.php"
echo '===RUN_TIMER_NOW==='
set +e
timeout 600 su -s /bin/bash web -c "flock -n /tmp/ozonstocksync-action-timer.lock /usr/bin/php $PLUGIN/lib/cli/action_timer_worker.php" >/tmp/ozs-rotation-check-worker.log 2>&1
RC=$?
set -e
echo "TIMER_EXIT=$RC"
echo '===TIMER_RESULT==='
cat "$ROOT/wa-data/protected/shop/plugins/ozonstocksync/action_timer_status.json" 2>/dev/null || echo 'TIMER_RESULT_MISSING'
echo
echo '===RECENT_STOCK_PRICE_SYNC_LOGS==='
python3 - <<'PY'
import glob,os,re
root='/home/web/vm-23f9aff9.na4u.ru/www/wa-log'
paths=[p for p in glob.glob(root+'/ozonstocksync*.log') if 'action-timer' not in p and 'commissions' not in p]
for p in sorted(paths):
    try:
        with open(p,encoding='utf-8',errors='replace') as f:
            from collections import deque
            lines=deque(f,maxlen=14000)
        selected=[line.strip() for line in lines if any(x in line for x in ('OK_STOCKS','OK_PRICES','ERROR_STOCKS','ERROR_PRICES','FINISH_ACCOUNTS','ACCOUNT_MODE'))]
        print(os.path.basename(p),'RECENT_MATCHES=',len(selected))
        for line in selected[-6:]:
            line=re.sub(r'(?i)(Api-Key|Client-Id|Authorization)\s*[:=]\s*[^\s,]+',r'\1=REDACTED',line)
            print(line[:350])
    except (OSError,Exception) as exc:
        print(os.path.basename(p),'READ_ERROR=',type(exc).__name__)
PY
exit 0
'''
            options = ["-i", private, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                       "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=15"]
            proc = subprocess.run(["ssh", *options, "root@" + HOST, "bash -se"], input=remote,
                                  text=True, capture_output=True, timeout=690)
            report = proc.stdout + "\nSSH_EXIT=" + str(proc.returncode) + "\n"
            if proc.stderr.strip() and proc.returncode:
                report += "SSH_ERROR=" + proc.stderr.strip()[-250:] + "\n"
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(report, encoding="utf-8")
            print(report[-25000:])
            if proc.returncode:
                raise RuntimeError("SSH server inspection failed")
        finally:
            try:
                request(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM}/ssh/{key_id}/",
                        "DELETE", None, headers)
            except Exception:
                print("TEMPORARY_SSH_KEY_CLEANUP_WARNING")

if __name__ == "__main__":
    main()
