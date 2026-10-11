#!/usr/bin/env python3
"""Stage49: apply second contact phone to two audited public CMS pages only."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r'''set -eu
root=/home/web/vm-23f9aff9.na4u.ru
backup=$(mktemp -d "$root/storefront-backups/stage49-cms-two-phones-XXXXXXXX")
chmod 700 "$backup"
script=$(mktemp /tmp/stage49-cms-XXXXXX.php)
jsonbackup=$(mktemp /tmp/stage49-original-XXXXXX.json)
chown web:web "$jsonbackup"
chmod 600 "$jsonbackup"
trap 'rm -f "$script" "$jsonbackup"' EXIT
cat > "$script" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$model=new shopPageModel();
$targets=array(
 22=>array('url'=>'garantiya/','name'=>'Гарантия','count'=>2),
 23=>array('url'=>'vozmozhnosti/','name'=>'Возможности','count'=>1)
);
$from='<a href="tel:+78122449264">+7 (812) 244-92-64</a>';
$to='<a href="tel:+78122449264">+7 (812) 244-92-64</a> (Санкт-Петербург) или <a href="tel:+74996776332">+7 (499) 677-63-32</a> (Москва)';
// Safe re-run: the two pages may already be updated by an earlier successful run.
$all_present=true;
foreach($targets as $id=>$t){
 $entry=$model->getById($id);
 if(!$entry || $entry['domain']!=='profikompany.ru' || $entry['url']!==$t['url'] ||
    substr_count((string)$entry['content'],'tel:+74996776332')!==$t['count'] ||
    substr_count((string)$entry['content'],'tel:+78122449264')!==$t['count']){
   $all_present=false;
   break;
 }
}
if($all_present){
 echo 'STAGE49_ALREADY_APPLIED='.json_encode(array('ok'=>true,'pages'=>array_keys($targets),'writes'=>0),JSON_UNESCAPED_UNICODE)."\n";
 exit(0);
}
$old=array();$changes=array();
foreach($targets as $id=>$target){
 $r=$model->getById($id);
 if(!$r || $r['domain']!=='profikompany.ru' || $r['url']!==$target['url'] || $r['name']!==$target['name'])
  throw new Exception('CMS page identity changed: '.$id);
 $body=(string)$r['content'];
 if(substr_count($body,$from)!==$target['count'] || strpos($body,'tel:+74996776332')!==false)
  throw new Exception('CMS contact source no longer matches '.$id);
 $old[$id]=$body;
 $new=str_replace($from,$to,$body);
 if(substr_count($new,'tel:+74996776332')!==$target['count'])throw new Exception('Unexpected Moscow phone count '.$id);
 $changes[$id]=$new;
}
$path=getenv('STAGE49_DB_BACKUP');
if(!$path || file_put_contents($path,json_encode($old,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES))===false)
 throw new Exception('Could not back up original CMS contents');
$updated=array();
try{
 foreach($changes as $id=>$new){
  $model->updateById($id,array('content'=>$new));
  $updated[]=$id;
 }
 $checks=array();
 foreach($targets as $id=>$t){
  $url='https://profikompany.ru/'.$t['url'].'?stage49_contact_check='.getmypid();
  $target='/tmp/stage49-contact-result-'.getmypid().'-'.$id.'.html';
  $log=array();$rc=0;
  exec('curl -ksSL --max-time 35 -o '.escapeshellarg($target)." -w '%{http_code}' ".escapeshellarg($url),$log,$rc);
  $body=@file_get_contents($target);@unlink($target);
  $status=trim(implode('',$log));
  $stored=$model->getById($id);
  $n=$stored?substr_count((string)$stored['content'],'href="tel:+74996776332"'):0;
  $ok=$rc===0 && $status==='200' && $n===$t['count'] &&
      $body!==false && strpos($body,'href="tel:+78122449264"')!==false &&
      strpos($body,'rel="canonical"')!==false;
  $checks[]=array('url'=>$t['url'],'http'=>$status,'body_moscow_links'=>$n,'ok'=>$ok);
  if(!$ok)throw new Exception('CMS phone public validation failed: '.$t['url'].' '.json_encode($checks));
 }
 echo 'STAGE49_APPLY='.json_encode(array('ok'=>true,'updated_page_ids'=>$updated,'checks'=>$checks),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}catch(Throwable $e){
 foreach($updated as $id) $model->updateById($id,array('content'=>$old[$id]));
 echo 'STAGE49_ROLLBACK='.json_encode(array('reason'=>$e->getMessage(),'restored_ids'=>$updated),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
 throw $e;
}
?>
PHP
chmod 644 "$script"
set +e
su -s /bin/bash web -c "STAGE49_DB_BACKUP='$jsonbackup' php -d display_errors=1 -d log_errors=0 '$script'"
status=$?
set -e
if test -s "$jsonbackup"; then cp "$jsonbackup" "$backup/cms_pages.json"; chmod 600 "$backup/cms_pages.json"; fi
printf 'STAGE49_BACKUP=%s\n' "$backup"
exit "$status"
'''
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token unavailable')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage49-phone-') as d:
  ssh=d+'/id_ed25519'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  payload=json.dumps({'key':pub,'name':'stage49-phone-'+os.getenv('GITHUB_RUN_ID','manual')}).encode()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',payload,headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Could not register temporary SSH key')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
       '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
       input=REMOTE,text=True,capture_output=True,timeout=170)
   for line in p.stdout.splitlines():
     if line.startswith('STAGE49_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('CMS phone update failed stdout='+p.stdout[-600:]+' stderr='+p.stderr[-600:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: temporary SSH key cleanup needs review')
if __name__=='__main__':run()
