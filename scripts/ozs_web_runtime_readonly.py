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
            json.dumps({'key':pub,'name':'ozs-web-runtime-readonly'}).encode(),h)['id']
    try:
        php='''<?php
        $root='/home/web/vm-23f9aff9.na4u.ru/www';
        chdir($root);require_once $root.'/wa-config/SystemConfig.class.php';
        waSystem::getInstance(null,new SystemConfig());wa('shop');
        $p=wa('shop')->getPlugin('ozonstocksync',true);
        echo 'VERSION='.$p->getVersion()."\\n";
        $rate=wa()->getDataPath('plugins/ozonstocksync/rfbs_live_rates.json',false,'shop',true);
        $state=wa()->getDataPath('plugins/ozonstocksync/rfbs_refresh_status.json',false,'shop',true);
        echo 'CACHE_EXISTS='.(is_readable($rate)?'YES':'NO')."\\n";
        if(is_readable($rate)){
            $json=json_decode(file_get_contents($rate),true);
            echo 'CACHE_TYPES='.count($json['types']??array())."\\n";
            echo 'CACHE_OFFERS='.($json['offers']??0)."\\n";
            echo 'CACHE_OFFICE='.json_encode($json['types']['офисное кресло']??null,JSON_UNESCAPED_UNICODE)."\\n";
            echo 'CACHE_TIME='.date('c',$json['updated_at']??0)."\\n";
        }
        echo 'REFRESH_STATUS='.(is_readable($state)?file_get_contents($state):'none')."\\n";
        ?>'''
        remote="cat >/tmp/ozs_web_runtime.php <<'PHP'\n"+php+"\nPHP\nchown web:web /tmp/ozs_web_runtime.php\nsu -s /bin/bash web -c 'php /tmp/ozs_web_runtime.php'\nstatus=$?\nrm -f /tmp/ozs_web_runtime.php\nexit $status"
        cmd=['ssh','-i',key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
             '-o','UserKnownHostsFile=/dev/null','root@45.86.180.49',remote]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=150)
        report=p.stdout+'\nEXIT='+str(p.returncode)+'\n'
        dest=pathlib.Path('.deploy-probe/ozs-web-runtime-readonly.txt')
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(report)
        print(report)
        if p.returncode:raise RuntimeError('Runtime read failed')
    finally:
        try:req('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/'+str(kid)+'/','DELETE',None,h)
        except Exception:pass
