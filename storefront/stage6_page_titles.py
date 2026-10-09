#!/usr/bin/env python3
"""Safely improve metadata of existing payment and delivery CMS pages only."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r'''set -eu
root=/home/web/vm-23f9aff9.na4u.ru
backup=$(mktemp -d "$root/storefront-backups/stage6-page-titles-XXXXXXXX")
chmod 700 "$backup"
tmp_backup=$(mktemp /tmp/storefront-page-title-backup-XXXXXXXX.json)
chown web:web "$tmp_backup"
chmod 600 "$tmp_backup"
p=$(mktemp /tmp/storefront-meta-XXXXXX.php)
trap 'rm -f "$p" "$tmp_backup"' EXIT
cat > "$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new shopPageModel();
$spec=array(
 10=>array('url'=>'oplata/','name'=>'Оплата','title'=>'Оплата заказа: способы и условия | Мегаполис'),
 12=>array('url'=>'dostavka/','name'=>'Доставка','title'=>'Доставка мебели по Санкт-Петербургу и России | Мегаполис')
);
$old=array();
foreach($spec as $id=>$cfg){
 $row=$m->getById($id);
 if(!$row || $row['domain']!=='profikompany.ru' || $row['route']!=='*'
    || $row['url']!==$cfg['url'] || $row['name']!==$cfg['name']){
   throw new Exception('Page identity mismatch '.$id);
 }
 $old[$id]=$row;
}
$backup=getenv('STOREFRONT_META_BACKUP');
if(!$backup || file_put_contents($backup,json_encode($old,JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT))===false){
  throw new Exception('Metadata backup failed');
}
$changed=array();
try {
 foreach($spec as $id=>$cfg){
  $m->updateById($id,array('title'=>$cfg['title']));
  $changed[]=$id;
 }
 $verify=array();
 foreach($spec as $id=>$cfg){
  $url='https://profikompany.ru/'.$cfg['url'].'?metadata_test='.getmypid();
  $file='/tmp/storefront-page-meta-'.getmypid();
  $out=array();$rc=0;
  exec('curl -k -sSL --max-time 35 -w \'%{http_code}\' -o '.escapeshellarg($file).' '.escapeshellarg($url),$out,$rc);
  $body=@file_get_contents($file);
  @unlink($file);
  $status=trim(implode('',$out));
  $ok=$rc===0 && $status==='200' && $body!==false
     && strpos($body,$cfg['title'])!==false;
  $verify[]=array('url'=>$cfg['url'],'http'=>$status,'title_visible'=>$ok);
  if(!$ok)throw new Exception('SEO title verification failed for '.$cfg['url'].' HTTP='.$status);
 }
 echo 'STOREFRONT_META_APPLY='.json_encode(array('success'=>true,'pages'=>$verify),JSON_UNESCAPED_UNICODE)."\n";
} catch(Throwable $e){
 foreach($changed as $id){
  $m->updateById($id,array('title'=>$old[$id]['title']));
 }
 echo 'STOREFRONT_META_ROLLBACK='.json_encode(array('reason'=>$e->getMessage(),'restored'=>$changed),JSON_UNESCAPED_UNICODE)."\n";
 throw $e;
}
?>
PHP
chmod 644 "$p"
set +e
su -s /bin/bash web -c "STOREFRONT_META_BACKUP='$tmp_backup' php -d display_errors=1 -d log_errors=0 '$p'"
status=$?
set -e
if test -s "$tmp_backup"; then
  cp "$tmp_backup" "$backup/old-pages.json"
  chmod 600 "$backup/old-pages.json"
fi
printf 'STOREFRONT_META_BACKUP=%s\n' "$backup"
exit "$status"
'''
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token unavailable')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='storefront-metadata-') as d:
  ssh=d+'/id_ed25519'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  payload=json.dumps({'key':pub,'name':'storefront-metadata-'+os.getenv('GITHUB_RUN_ID','manual')}).encode()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',payload,headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Could not register temporary SSH key')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
       '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
       input=REMOTE,text=True,capture_output=True,timeout=170)
   for line in p.stdout.splitlines():
     if line.startswith('STOREFRONT_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Title deploy failed stdout='+p.stdout[-600:]+' stderr='+p.stderr[-600:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: temporary SSH key cleanup needs review')
if __name__=='__main__':run()
