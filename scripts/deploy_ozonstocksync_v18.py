#!/usr/bin/env python3
"""Deploy Ozon Stock Sync v1.8.0 safely using the existing Netangels VM SSH API."""
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.parse
import urllib.request

HOST = '45.86.180.49'
VM = 44780
ROOT = '/home/web/vm-23f9aff9.na4u.ru/www'
REPORT = pathlib.Path('.deploy-probe/ozonstocksync-v18-deploy.txt')

def request(url, method='GET', data=None, headers=None):
    q = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    with urllib.request.urlopen(q, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

def main():
    api_key = os.environ['NETANGELS_API_KEY'].strip()
    token = request('https://panel.netangels.ru/api/gateway/token/', 'POST',
                    urllib.parse.urlencode({'api_key': api_key}).encode(),
                    {'Content-Type': 'application/x-www-form-urlencoded'})['token']
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    archive = '/tmp/ozonstocksync-v18-' + os.environ.get('GITHUB_RUN_ID', 'manual') + '.tgz'
    with tempfile.TemporaryDirectory() as tmp:
        key = tmp + '/id'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key], check=True)
        pub = pathlib.Path(key + '.pub').read_text().strip()
        key_id = request('https://api-ms.netangels.ru/api/v1/cloud/vms/%d/ssh/create/' % VM,
                         'POST', json.dumps({'key': pub, 'name': 'ozs-v18-deploy'}).encode(), headers)['id']
        ssh_args = ['-i', key, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                    '-o', 'UserKnownHostsFile=/dev/null']
        try:
            scp = subprocess.run(['scp'] + ssh_args +
                                 ['/tmp/ozonstocksync-v18.tgz', 'root@' + HOST + ':' + archive],
                                 capture_output=True, text=True, timeout=120)
            if scp.returncode:
                raise RuntimeError('archive transfer failed')
            script = r'''set -Eeuo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/ozonstocksync"
PARENT="$ROOT/wa-apps/shop/plugins"
ARCHIVE=''' + archive + r'''
DIR="$ROOT/wa-data/protected/shop/ozonstocksync-backups"
mkdir -p "$DIR"
BACKUP="$DIR/ozonstocksync-v18-$(date +%Y%m%d-%H%M%S).tgz"
tar -czf "$BACKUP" -C "$PARENT" ozonstocksync
echo "BACKUP=$BACKUP"
DEPLOY_OK=0
rollback() {
    RC=$?
    if [ "$DEPLOY_OK" != 1 ]; then
        tar -xzf "$BACKUP" -C "$PARENT" && chown -R web:web "$PLUGIN"
        echo "ROLLBACK=OK"
    fi
    rm -f "$ARCHIVE" /tmp/ozs_commission_health.php
    exit "$RC"
}
trap rollback EXIT
tar -xzf "$ARCHIVE" -C "$PLUGIN"
chown -R web:web "$PLUGIN"
find "$PLUGIN" -name '*.php' -print0 | while IFS= read -r -d '' file; do php -l "$file"; done
cat >/tmp/ozs_commission_health.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('ozonstocksync',true);
$controls=$p->getControls(array('namespace'=>'shop_ozonstocksync'));
$html=isset($controls['ozonstocksync_ui'])?(string)$controls['ozonstocksync_ui']:'';
$ok=$p->getVersion()==='1.8.0' &&
    strpos($html,'ozs-rate-refresh')!==false &&
    strpos($html,'type_margin_percent')!==false &&
    file_exists($root.'/wa-apps/shop/plugins/ozonstocksync/lib/cli/shopOzonstocksyncPluginCommissions.cli.php');
echo 'VERSION='.$p->getVersion()."\n";
echo 'BUTTON='. (strpos($html,'ozs-rate-refresh')!==false?'YES':'NO')."\n";
echo 'UI_HEALTH='.($ok?'OK':'FAIL')."\n";
exit($ok?0:2);
PHP
chown web:web /tmp/ozs_commission_health.php
su -s /bin/bash web -c 'php -d display_errors=0 /tmp/ozs_commission_health.php'
command -v crontab >/dev/null
OLD_CRON=$(crontab -u web -l 2>/dev/null || true)
CMD="flock -n /tmp/ozonstocksync-commissions.lock /usr/bin/php $ROOT/cli.php shop ozonstocksyncPluginCommissions"
{
    printf "%s\n" "$OLD_CRON" | grep -v "ozonstocksyncPluginCommissions" || true
    echo "* * * * * $CMD queued_only=1 >> $ROOT/wa-log/ozonstocksync-commissions-cron.log 2>&1"
    echo "10 4 * * * $CMD >> $ROOT/wa-log/ozonstocksync-commissions-cron.log 2>&1"
} | crontab -u web -
crontab -u web -l | grep 'ozonstocksyncPluginCommissions'
echo 'COMMISSION_CRON=OK'
DEPLOY_OK=1
echo 'DEPLOY_STATUS=OK'
'''
            run = subprocess.run(['ssh'] + ssh_args + ['root@' + HOST, 'bash -se'],
                                 input=script, capture_output=True, text=True, timeout=400)
            text = run.stdout
            if run.returncode:
                text += '\nDEPLOY_STATUS=FAILED\n'
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(text)
            print(text)
            if run.returncode:
                raise RuntimeError('remote deployment failed (exit %s)' % run.returncode)
        finally:
            try:
                request('https://api-ms.netangels.ru/api/v1/cloud/vms/%d/ssh/%s/' % (VM, key_id),
                        'DELETE', None, headers)
            except Exception:
                print('SSH_KEY_CLEANUP_WARNING')

if __name__ == '__main__':
    main()
