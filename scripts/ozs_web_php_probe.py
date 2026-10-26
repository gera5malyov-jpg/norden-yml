import json, os, pathlib, subprocess, tempfile, urllib.parse, urllib.request
def request(url, method='GET', body=None, headers=None):
    q=urllib.request.Request(url,method=method,data=body,headers=headers or {})
    with urllib.request.urlopen(q,timeout=30) as r:
        v=r.read().decode()
        return json.loads(v) if v else {}
t=request('https://panel.netangels.ru/api/gateway/token/','POST',
    urllib.parse.urlencode({'api_key':os.environ['NETANGELS_API_KEY']}).encode(),
    {'Content-Type':'application/x-www-form-urlencoded'})['token']
h={'Authorization':'Bearer '+t,'Content-Type':'application/json'}
with tempfile.TemporaryDirectory() as td:
    private=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
    pub=pathlib.Path(private+'.pub').read_text().strip()
    key_id=request('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/create/','POST',
        json.dumps({'key':pub,'name':'ozs-php-binary-probe'}).encode(),h)['id']
    try:
        remote="echo DEFAULT=$(command -v php); su -s /bin/bash web -c 'echo WEB_PHP=$(command -v php); echo MYSQLI=$(php -m | grep -ic mysqli || true); php -r \"echo PHP_BINARY,PHP_EOL;\"'; ls -l /usr/bin/php"
        cmd=['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
             '-o','UserKnownHostsFile=/dev/null','root@45.86.180.49',remote]
        p=subprocess.run(cmd,text=True,capture_output=True,timeout=90)
        report=p.stdout+'\nEXIT='+str(p.returncode)+'\n'
        print(report)
        dest=pathlib.Path('.deploy-probe/ozs-web-php-probe.txt')
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(report)
        if p.returncode:raise RuntimeError('PHP path probe failed')
    finally:
        try:request('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/'+str(key_id)+'/','DELETE',None,h)
        except Exception:pass
