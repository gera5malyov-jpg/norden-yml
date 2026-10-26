#!/usr/bin/env python3
"""Read-only commission refresh on production, with a shared lock and verified report."""
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.parse
import urllib.request

def req(url, method='GET', body=None, headers=None):
    q = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    with urllib.request.urlopen(q, timeout=30) as r:
        value = r.read().decode('utf-8')
        return json.loads(value) if value else {}

key_value = os.environ['NETANGELS_API_KEY'].strip()
token = req('https://panel.netangels.ru/api/gateway/token/', 'POST',
            urllib.parse.urlencode({'api_key': key_value}).encode(),
            {'Content-Type': 'application/x-www-form-urlencoded'})['token']
headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
output = pathlib.Path('.deploy-probe/ozonstocksync-v18-initial-rfbs-refresh.txt')
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as td:
    private = td + '/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private], check=True)
    pub = pathlib.Path(private+'.pub').read_text().strip()
    kid = req('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/create/', 'POST',
              json.dumps({'key':pub,'name':'ozs-rfbs-manual-check'}).encode(), headers)['id']
    try:
        root = '/home/web/vm-23f9aff9.na4u.ru/www'
        remote = ("cd " + root + " && flock -n /tmp/ozonstocksync-commissions.lock " +
                  "su -s /bin/bash web -c '/usr/bin/php cli.php shop ozonstocksyncPluginCommissions'")
        cmd=['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
             '-o','UserKnownHostsFile=/dev/null','root@45.86.180.49',remote]
        result=subprocess.run(cmd, capture_output=True,text=True,timeout=780)
        data=result.stdout[-9000:] + '\nCLI_EXIT=' + str(result.returncode) + '\n'
        output.write_text(data)
        print(data)
        if result.returncode:
            raise RuntimeError('Commission-only CLI returned nonzero exit code')
    finally:
        try:
            req('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/'+str(kid)+'/', 'DELETE',None,headers)
        except Exception:
            print('EPHEMERAL_SSH_KEY_CLEANUP_WARNING')
