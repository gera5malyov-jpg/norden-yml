#!/usr/bin/env python3
import json, os, pathlib, subprocess, tempfile, urllib.parse, urllib.request
def req(url, method='GET', body=None, headers=None):
    request=urllib.request.Request(url,method=method,data=body,headers=headers or {})
    with urllib.request.urlopen(request,timeout=30) as r:
        data=r.read().decode()
        return json.loads(data) if data else {}
tok=req('https://panel.netangels.ru/api/gateway/token/','POST',
        urllib.parse.urlencode({'api_key':os.environ['NETANGELS_API_KEY']}).encode(),
        {'Content-Type':'application/x-www-form-urlencoded'})['token']
h={'Authorization':'Bearer '+tok,'Content-Type':'application/json'}
with tempfile.TemporaryDirectory() as td:
    key=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key],check=True)
    pub=pathlib.Path(key+'.pub').read_text().strip()
    kid=req('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/create/','POST',
            json.dumps({'key':pub,'name':'ozs-v18-cache-check'}).encode(),h)['id']
    try:
        command=r'''ROOT=/home/web/vm-23f9aff9.na4u.ru/www
cd "$ROOT"
echo SERVER_TIME=$(date '+%Y-%m-%d %H:%M:%S %z')
php -r 'require_once "wa-config/SystemConfig.class.php"; waSystem::getInstance(null,new SystemConfig()); wa("shop"); $p=wa("shop")->getPlugin("ozonstocksync"); $f=wa()->getDataPath("plugins/ozonstocksync/rfbs_live_rates.json",false,"shop",true); $s=wa()->getDataPath("plugins/ozonstocksync/rfbs_refresh_status.json",false,"shop",true); echo "VERSION=".$p->getVersion()."\n"; echo "CACHE_EXISTS=".(is_readable($f)?"YES":"NO")."\n"; if(is_readable($f)){$v=json_decode(file_get_contents($f),true);echo "CACHED_TYPES=".count($v["types"]??array())."\n";echo "RATE_OFFICE_CHAIR=".json_encode($v["types"]["офисное кресло"]??null,JSON_UNESCAPED_UNICODE)."\n";echo "UPDATED_AT=".date("c",$v["updated_at"]??0)."\n";} echo "JOB_STATUS=".(is_readable($s)?file_get_contents($s):"NONE")."\n";'
echo CRON_COUNT=$(crontab -u web -l | grep -c ozonstocksyncPluginCommissions)
'''
        cmd=['ssh','-i',key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
             '-o','UserKnownHostsFile=/dev/null','root@45.86.180.49',command]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=150)
        output=result.stdout+'\nSSH_EXIT='+str(result.returncode)+'\n'
        dest=pathlib.Path('.deploy-probe/ozonstocksync-v18-runtime-state.txt')
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(output)
        print(output)
        if result.returncode: raise RuntimeError('Runtime check failed')
    finally:
        try:req('https://api-ms.netangels.ru/api/v1/cloud/vms/44780/ssh/'+str(kid)+'/','DELETE',None,h)
        except Exception:pass
