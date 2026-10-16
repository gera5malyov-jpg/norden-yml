#!/usr/bin/env python3
"""Read-only audit of Aletan YML Pro product-matching state on owned VM."""
import json, os, subprocess, tempfile, urllib.parse, urllib.request
from pathlib import Path

VM_ID=44780
VM_IP="45.86.180.49"

def api(url,method="GET",data=None,headers=None):
    request=urllib.request.Request(url,data=data,headers=headers or {},method=method)
    with urllib.request.urlopen(request,timeout=25) as r:
        result=r.read().decode("utf-8")
        return json.loads(result) if result else {}

def main():
    key=os.environ["NETANGELS_API_KEY"].strip()
    token=api("https://panel.netangels.ru/api/gateway/token/","POST",
              urllib.parse.urlencode({"api_key":key}).encode(),
              {"Content-Type":"application/x-www-form-urlencoded"})["token"]
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory() as temp:
        identity=Path(temp)/"id"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(identity)],check=True)
        pub=Path(str(identity)+".pub").read_text().strip()
        r=api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/","POST",
              json.dumps({"key":pub,"name":"aletan-ymlpro-audit-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        key_id=r["id"]
        try:
            script=r"""set -eu
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
BASE="$ROOT/wa-apps/shop/plugins/yml/lib/classes"
for spec in  'import/pins/shopYmlOfferIndexService.class.php:1:170'  'import/pins/shopYmlOfferMatcher.class.php:1:185'  'import/finders/cache/shopYmlFinderCache.class.php:105:195'  'binding/shopYmlBinding.class.php:1:135'  'import/finders/scope/shopYmlProductScopeProvider.class.php:1:125'; do
    file=${spec%%:*}; part=${spec#*:}; lo=${part%%:*}; hi=${part##*:}
    if test -f "$BASE/$file"; then
        echo "=====FILE:$file:$lo-$hi====="
        sed -n "${lo},${hi}p" "$BASE/$file" | head -185
    fi
done
echo '=====PHP DATABASE INSPECTION====='
cat > /tmp/aletan_ymlpro_readonly.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root); require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig()); wa('shop');
$m=new waModel();
$tables=$m->query("SHOW TABLES LIKE '%yml%'")->fetchAll();
echo "YML_TABLES ".json_encode($tables,JSON_UNESCAPED_UNICODE).PHP_EOL;
foreach(array('B1001','B102','B307') as $code) {
  $p=$m->query("SELECT id, name, type_id, yml_id, yml_profile_id, edit_datetime FROM shop_product WHERE yml_id=s:code",array('code'=>$code))->fetchAll();
  echo "PRODUCT ".json_encode($p,JSON_UNESCAPED_UNICODE).PHP_EOL;
}
$possible=array('shop_yml_offer_index','shop_yml_offer','shop_yml_profile','shop_yml_profile_settings');
foreach($possible as $t) {
  try { $res=$m->query("SHOW COLUMNS FROM `".$t."`")->fetchAll();
    if($res) echo "COLUMNS ".$t." ".json_encode(array_column($res,'Field'),JSON_UNESCAPED_UNICODE).PHP_EOL;
  } catch(Throwable $e){}
}
?>
PHP
chown web:web /tmp/aletan_ymlpro_readonly.php
su -s /bin/bash web -c 'php /tmp/aletan_ymlpro_readonly.php'
rm -f /tmp/aletan_ymlpro_readonly.php
echo '=====AUDIT_COMPLETE====='
"""
            proc=subprocess.run(["ssh","-i",str(identity),"-o","BatchMode=yes",
                                  "-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null",
                                  "-o","ConnectTimeout=12","root@"+VM_IP,"bash","-s"],
                                 input=script,text=True,capture_output=True,timeout=155)
            print(proc.stdout[-25000:])
            if proc.stderr:print("STDERR",proc.stderr[-900:])
            if proc.returncode:raise RuntimeError(f"SSH status {proc.returncode}")
        finally:
            api(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/","DELETE",None,headers)

if __name__=="__main__":
    main()
