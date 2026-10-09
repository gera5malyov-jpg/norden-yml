#!/usr/bin/env python3
"""Repair native Webasyst consent-document URLs and add legal footer link."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json

REMOTE=r'''set -eu
root=/home/web/vm-23f9aff9.na4u.ru
mkdir -p "$root/storefront-backups"
backup=$(mktemp -d "$root/storefront-backups/stage4-agreements-XXXXXXXX")
chmod 700 "$backup"
cp "$root/www/wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html" "$backup/layout.footer.html"
chmod 600 "$backup/layout.footer.html"
tmpdir=$(mktemp -d /tmp/storefront-agreement-XXXXXXXX)
chown web:web "$tmpdir"
chmod 700 "$tmpdir"
p=$(mktemp /tmp/storefront-agree-XXXXXX.php)
trap 'rm -f "$p"; rm -rf "$tmpdir"' EXIT
cat >"$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel('wa_agreement_document');
$ids=array(2,4,6);
$old=array();
foreach($ids as $id){
 $doc=$m->getById($id);
 if(!$doc || $doc['domain']!=='profikompany.ru' || $doc['app_id']!=='shop'){
    throw new Exception('Agreement id not validated: '.$id);
 }
 $old[$id]=$doc;
}
if(strpos($old[4]['document_text'],'---https://profikompany.ru/dostavka/---')===false){
 throw new Exception('Malformed consent link changed, no writes performed');
}
if(strpos($old[6]['document_text'],'href=""')===false){
 throw new Exception('Blank shipping agreement link changed, no writes performed');
}
$footer=$root.'/wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html';
$old_footer=file_get_contents($footer);
if($old_footer===false || substr_count($old_footer,'Terms of delivery')!==1 || strpos($old_footer,'href="/privacy-policy/"')!==false){
 throw new Exception('Footer template changed or already has privacy link');
}
$backup=getenv('STOREFRONT_BACKUP_TEMP');
if(!$backup || file_put_contents($backup,json_encode($old,JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT))===false){
 throw new Exception('Agreement backup file could not be written; no changes');
}
@chmod($backup,0600);
$target='/privacy-policy/';
$link='<a href="'.$target.'" target="_blank" rel="noopener noreferrer">политикой обработки персональных данных</a>';
$new=array();
$new[2]='Я даю согласие ООО «Мегаполис» (ИНН 7838104843) на обработку указанных мной имени и контактных данных для регистрации учётной записи и связи по вопросам её использования. С '.$link.' ознакомлен(а).';
$new[4]='Я даю согласие ООО «Мегаполис» (ИНН 7838104843) на обработку указанных мной имени, телефона, электронной почты и адреса доставки для оформления и исполнения заказа. С '.$link.' ознакомлен(а).';
$new[6]='Оформляя заказ, вы подтверждаете своё совершеннолетие. Ознакомьтесь с <a href="'.$target.'" target="_blank" rel="noopener noreferrer">политикой обработки персональных данных</a>.';
$new_footer='';
foreach(preg_split('/(?<=\n)/',$old_footer) as $line){
 $new_footer.=$line;
 if(strpos($line,'Terms of delivery')!==false){
  $new_footer.='              <li class="footer__list-item"><a href="/privacy-policy/" class="footer__link">Политика обработки персональных данных</a></li>'."\n";
 }
}
if(substr_count($new_footer,'href="/privacy-policy/"')!==1)throw new Exception('Footer link injection preflight failed');
$did_write=false;
try{
 foreach($new as $id=>$txt){$m->updateById($id,array('document_text'=>$txt));$did_write=true;}
 if(file_put_contents($footer,$new_footer)===false){throw new Exception('Footer write failed');}
 $reloaded=$m->getById(4);
 if(!isset($reloaded['document_text']) || strpos($reloaded['document_text'],'href="/privacy-policy/"')===false){
  throw new Exception('Native agreement readback failed');
 }
 $out=array();$rc=0;
 exec("curl -k -sS -L --max-time 30 -w '%{http_code}' -o /tmp/storefront-stage4-check-".getmypid()." https://profikompany.ru/", $out,$rc);
 $body=@file_get_contents('/tmp/storefront-stage4-check-'.getmypid());
 @unlink('/tmp/storefront-stage4-check-'.getmypid());
 $status=trim(implode('',$out));
 if($rc!==0 || $status!=='200' || $body===false || strpos($body,'href="https://profikompany.ru/privacy-policy/"')===false){
  throw new Exception('Homepage footer smoke check failed, http='.$status);
 }
 echo 'STOREFRONT_AGREEMENTS_APPLY='.json_encode(array('ok'=>true,'docs'=>array(2,4,6),
 'footer'=>'wa-data/public/site/themes/pureMegapolis42/layouts/layout.footer.html',
 'http_status'=>$status,'policy_url'=>'https://profikompany.ru/privacy-policy/'),JSON_UNESCAPED_UNICODE)."\n";
}catch(Throwable $e){
 if($did_write){foreach($old as $id=>$doc){$m->updateById($id,array('document_text'=>$doc['document_text']));}}
 if($old_footer!==false){file_put_contents($footer,$old_footer);}
 echo 'STOREFRONT_AGREEMENTS_ROLLBACK='.json_encode(array('reason'=>$e->getMessage(),'restored'=>true),JSON_UNESCAPED_UNICODE)."\n";
 throw $e;
}
?>
PHP
chmod 644 "$p"
set +e
su -s /bin/bash web -c "STOREFRONT_BACKUP_TEMP='$tmpdir/agreements.json' php '$p'"
status=$?
set -e
if test -f "$tmpdir/agreements.json"; then
  cp "$tmpdir/agreements.json" "$backup/agreements.json"
  chmod 600 "$backup/agreements.json"
fi
printf 'STOREFRONT_AGREEMENTS_BACKUP=%s\n' "$backup"
exit "$status"
'''

def run():
    key=os.environ.get("NETANGELS_API_KEY","").strip()
    if not key:raise RuntimeError("Missing NETANGELS_API_KEY")
    token=request_json("https://panel.netangels.ru/api/gateway/token/","POST",
        urllib.parse.urlencode({"api_key":key}).encode(),
        {"Content-Type":"application/x-www-form-urlencoded"}).get("token")
    if not token:raise RuntimeError("NetAngels gateway token missing")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"}
    with tempfile.TemporaryDirectory(prefix="storefront-agree-") as d:
        ssh=d+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",ssh],check=True)
        pub=Path(ssh+".pub").read_text().strip()
        reg=request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/".format(VM_ID),
            "POST",json.dumps({"key":pub,"name":"storefront-agree-"+os.getenv("GITHUB_RUN_ID","manual")}).encode(),headers)
        kid=reg.get("id")
        if not kid:raise RuntimeError("Temporary SSH key missing")
        try:
            p=subprocess.run(["ssh","-i",ssh,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
                "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15","root@"+VM_IP,"bash -s"],
                input=REMOTE,text=True,capture_output=True,timeout=170)
            for line in p.stdout.splitlines():
                if line.startswith("STOREFRONT_"):print(line,flush=True)
            if p.returncode:raise RuntimeError("Agreement deploy failed: "+p.stderr[-1000:])
        finally:
            try:
                request_json("https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/".format(VM_ID,kid),"DELETE",None,headers)
                print("temporary_ssh_key_removed=true")
            except Exception:
                print("WARNING: Temporary SSH key cleanup failed")

if __name__=="__main__":run()
