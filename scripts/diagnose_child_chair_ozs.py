import json, os, pathlib, subprocess, tempfile, urllib.parse, urllib.request
def req(url, method='GET', body=None, headers=None):
    r=urllib.request.Request(url,method=method,data=body,headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode()
        return json.loads(raw) if raw else {}
t=req('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':os.environ['NETANGELS_API_KEY']}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'})['token']
h={'Authorization':'Bearer '+t,'Content-Type':'application/json'}
with tempfile.TemporaryDirectory() as td:
    key=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key],check=True)
    pub=pathlib.Path(key+'.pub').read_text().strip()
    kid=req('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/create/','POST',
            json.dumps({'key':pub,'name':'ozs-child-chair-offer-audit'}).encode(),h)['id']
    try:
        php=pathlib.Path('scripts/diagnose_child_chair_ozs.php').read_text(encoding='utf-8')
        remote="cat >/tmp/ozs_child_chair_audit.php <<'PHP'\n"+php+"\nPHP\nchown web:web /tmp/ozs_child_chair_audit.php\nsu -s /bin/bash web -c 'php /tmp/ozs_child_chair_audit.php'\nstatus=$?\nrm -f /tmp/ozs_child_chair_audit.php\nexit $status"
        cmd=['ssh','-i',key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
             '-o','UserKnownHostsFile=/dev/null','root@45.86.180.49',remote]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=150)
        report=p.stdout+'\nEXIT='+str(p.returncode)+'\n'
        dest=pathlib.Path('.deploy-probe/ozs-child-chair-offer-audit.txt')
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(report)
        print(report)
        if p.returncode:raise RuntimeError('Runtime read failed')
    finally:
        try:req('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/'+str(kid)+'/','DELETE',None,h)
        except Exception:pass
