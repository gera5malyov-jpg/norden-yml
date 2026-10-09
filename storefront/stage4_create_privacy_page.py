#!/usr/bin/env python3
"""Create a new Shop-Script privacy page, never alter existing pages/orders."""
from __future__ import annotations
import base64,json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r'''set -eu
p=$(mktemp /tmp/storefront-privacy-page-XXXXXX.php)
trap 'rm -f "$p"' EXIT
cat >"$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new shopPageModel();
$policy=base64_decode('POLICY_BASE64_REPLACE_ME',true);
if(!$policy || mb_strlen($policy,'UTF-8')<2000){throw new Exception('Policy text missing');}
$existing=$m->getByField(array('domain'=>'profikompany.ru','route'=>'*','url'=>'privacy-policy/'));
if($existing){throw new Exception('Target page already exists, refusing to overwrite');}
$now=date('Y-m-d H:i:s');
$id=null;
try{
 $id=$m->insert(array(
  'domain'=>'profikompany.ru','route'=>'*',
  'name'=>'Политика обработки персональных данных',
  'title'=>'Политика обработки персональных данных | Мегаполис',
  'url'=>'privacy-policy/','full_url'=>'privacy-policy/',
  'content'=>$policy,
  'create_datetime'=>$now,'update_datetime'=>$now,
  'create_contact_id'=>1,'sort'=>99,'status'=>1,'parent_id'=>null
 ));
 if(!$id){throw new Exception('Could not create privacy policy page');}
 $cmd="curl -k -sSL --max-time 40 -w '%{http_code}' -o /tmp/storefront-policy-check-".(int)$id." https://profikompany.ru/privacy-policy/";
 $output=array();$code=0;
 exec($cmd,$output,$code);
 $body=@file_get_contents('/tmp/storefront-policy-check-'.(int)$id);
 @unlink('/tmp/storefront-policy-check-'.(int)$id);
 $status=trim(implode('',$output));
 $ok=$code===0 && $status==='200' && $body!==false
  && strpos($body,'ООО «Мегаполис»')!==false
  && strpos($body,'152-ФЗ')!==false
  && strpos($body,'privacy-policy/')!==false;
 if(!$ok){
   throw new Exception('Page smoke test failed status='.$status.' text_present='.(int)($body!==false && strpos($body,'152-ФЗ')!==false));
 }
 echo 'STOREFRONT_POLICY_CREATE='.json_encode(array('ok'=>true,'page_id'=>(int)$id,
 'url'=>'https://profikompany.ru/privacy-policy/','http_status'=>$status,'text_size'=>strlen($policy)),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}catch(Throwable $e){
 if($id){
  $m->deleteById($id);
 }
 echo 'STOREFRONT_POLICY_ROLLBACK='.json_encode(array('reason'=>$e->getMessage(),'new_page_removed'=>(bool)$id),JSON_UNESCAPED_UNICODE)."\n";
 throw $e;
}
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php $p"
'''

def run():
    policy=Path(__file__).with_name("privacy_policy_profikompany.html").read_bytes()
    remote=REMOTE.replace("POLICY_BASE64_REPLACE_ME",base64.b64encode(policy).decode("ascii"))
    key=os.environ.get("NETANGELS_API_KEY","").strip()
    if not key:raise RuntimeError("Missing NETANGELS_API_KEY")
    token=request_json("https://panel.netangels.ru/api/gateway/token/","POST",
        urllib.parse.urlencode({"api_key":key}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded"}).get("token")
    if not token:raise RuntimeError("NetAngels gateway unavailable")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory(prefix="storefront-policy-") as d:
        ssh=d+"/id"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",ssh],check=True)
        pub=Path(ssh+".pub").read_text().strip()
        reg=request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/".format(VM_ID),
            "POST",json.dumps({"key":pub,"name":"storefront-policy-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=reg.get("id")
        if not kid:raise RuntimeError("Temporary key unavailable")
        try:
            p=subprocess.run(["ssh","-i",ssh,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15","root@"+VM_IP,"bash -s"],
                input=remote,text=True,capture_output=True,timeout=160)
            for line in p.stdout.splitlines():
                if line.startswith("STOREFRONT_POLICY_"):print(line,flush=True)
            if p.returncode:raise RuntimeError("Page creation failed exit={} stderr={}".format(p.returncode,p.stderr[-700:]))
        finally:
            try:
                request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/".format(VM_ID,kid),"DELETE",None,headers)
                print("temporary_ssh_key_removed=true")
            except Exception:
                print("WARNING: temporary SSH key cleanup needs review")

if __name__=="__main__":run()
