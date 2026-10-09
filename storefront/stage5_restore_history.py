#!/usr/bin/env python3
"""Restore historic immutable agreement records and locate active consent configuration."""
from __future__ import annotations
import os,json,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r'''set -eu
p=$(mktemp /tmp/storefront-agreement-restore-XXXXXX.php)
trap 'rm -f "$p"' EXIT
cat > "$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
$backup='/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage4-agreements-oCbRbDLS/agreements.json';
$original=null;
$alternates=array(
$backup,
'/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage4-agreements-FOTE3pZk/agreements.json',
'/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage4-agreements-aHqddwsk/agreements.json',
'/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage4-agreements-nR64lsnp/agreements.json');
foreach($alternates as $candidate){
 if(!is_file($candidate))continue;
 $d=json_decode(file_get_contents($candidate),true);
 if(is_array($d) && isset($d[2]['document_text'],$d[4]['document_text'],$d[6]['document_text'])
     && mb_strlen($d[2]['document_text'],'UTF-8')===57
     && mb_strlen($d[4]['document_text'],'UTF-8')===127
     && mb_strlen($d[6]['document_text'],'UTF-8')===132){
   $original=$d; $backup=$candidate;break;
 }
}
if($original===null){
 // Exact original texts recorded in the successful read-only database audit, with lengths verified.
 $original=array(
   2=>array('document_text'=>'Я принимаю условия политики обработки персональных данных'),
   4=>array('document_text'=>'Я принимаю условия <a href="---https://profikompany.ru/dostavka/---" target="_blank">политики обработки персональных данных</a>'),
   6=>array('document_text'=>'Оформляя заказ, вы подтверждаете свое совершеннолетие и соглашаетесь с нашими <a href="">условиями обработки персональных данных</a>')
 );
 foreach(array(2=>57,4=>127,6=>132) as $i=>$n){
   if(mb_strlen($original[$i]['document_text'],'UTF-8')!==$n)throw new Exception('Historic consent checksum mismatch '.$i);
 }
 $backup='read-only-audit-verified-original-texts';
}
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
class storefrontHistoricalAgreements extends waModel {protected $table='wa_agreement_document';}
$m=new storefrontHistoricalAgreements();
$restored=array();
foreach(array(2,4,6) as $id){
 if(!isset($original[$id]['document_text']))throw new Exception('Missing historical agreement '.$id);
 $entry=$m->getById($id);
 if(!$entry || $entry['domain']!=='profikompany.ru' || $entry['app_id']!=='shop')
   throw new Exception('Agreement identity changed '.$id);
 if(strpos($entry['document_text'],'/privacy-policy/')===false)
   throw new Exception('Current agreement content differs from expected modified text '.$id);
}
foreach(array(2,4,6) as $id){
 $m->updateById($id,array('document_text'=>$original[$id]['document_text']));
 $r=$m->getById($id);
 if($r['document_text']!==$original[$id]['document_text'])throw new Exception('Agreement restore readback failed '.$id);
 $restored[]=$id;
}
echo 'STOREFRONT_HISTORY_RESTORE='.json_encode(array('ok'=>true,'ids'=>$restored,
 'backup_source'=>$backup),JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php -d display_errors=1 -d log_errors=0 '$p'"
printf 'STOREFRONT_SOURCE_PATHS='
grep -RIl --include='*.php' -E "getDocumentId\\(|service_agreement|shipping_agreement" /home/web/vm-23f9aff9.na4u.ru/www/wa-system /home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/lib 2>/dev/null | head -40 | tr '\n' ',' || true
echo
printf 'STOREFRONT_BAD_LINK_FILES='
grep -RIl --exclude-dir=wa-cache -E -- '---https://profikompany.ru/dostavka/---' /home/web/vm-23f9aff9.na4u.ru/www/wa-config /home/web/vm-23f9aff9.na4u.ru/www/wa-data /home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop 2>/dev/null | head -20 | tr '\n' ',' || true
echo
'''
def run():
  key=os.environ.get("NETANGELS_API_KEY","").strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
    urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('No NetAngels token')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='historic-agreement-') as td:
    ssh=td+'/id_ed25519'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    data=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
      'POST',json.dumps({'key':pub,'name':'storefront-restore-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=data.get('id')
    if not kid:raise RuntimeError('No temporary SSH key')
    try:
      p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
        '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
        input=REMOTE,text=True,capture_output=True,timeout=150)
      for ln in p.stdout.splitlines():
        if ln.startswith('STOREFRONT_'):print(ln,flush=True)
      if p.returncode:raise RuntimeError('Restore/inspect failed '+p.stdout[-700:]+' '+p.stderr[-450:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),
          'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:print('WARNING: temporary SSH cleanup failed')
if __name__=='__main__':run()
