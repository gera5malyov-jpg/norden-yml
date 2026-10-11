#!/usr/bin/env python3
"""Read-only search for lone phone links in public CMS pages."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
tmp=$(mktemp /tmp/stage48-cms-XXXXXX.php)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$model=new shopPageModel();
$pages=$model->getByField('domain','profikompany.ru',true);
$out=array();
foreach($pages as $page){
 if(isset($page['status']) && !$page['status'])continue;
 $content=isset($page['content'])?(string)$page['content']:'';
 $spb=substr_count($content,'tel:+78122449264');
 $msk=substr_count($content,'tel:+74996776332')+substr_count($content,'tel:+7(499)6776332');
 if($spb>0 || $msk>0){
   $out[]=array('id'=>$page['id'],'url'=>$page['url'],'name'=>$page['name'],'spb_links'=>$spb,'msk_links'=>$msk,
       'content_chars'=>mb_strlen($content,'UTF-8'));
 }
}
echo 'STAGE48_CONTACT_PAGES='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$tmp"
su -s /bin/bash web -c "php -d display_errors=1 -d log_errors=0 '$tmp'"
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage48-contacts-read-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  from urllib.error import HTTPError
  import time
  for attempt in range(1,11):
   try:
    resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage48-contacts-'+os.getenv('GITHUB_RUN_ID','audit')}).encode(),headers)
    break
   except HTTPError as e:
    details=e.read().decode('utf-8','replace')
    if e.code==400 and 'SSH' in details and attempt<10:
     print('STAGE48_SERVER_BACKUP='+json.dumps({'attempt':attempt,'retry_seconds':25}),flush=True)
     time.sleep(25)
     continue
    print('STAGE48_SSH_API_ERROR='+json.dumps({'status':e.code,'detail':details[:450]}),flush=True)
    raise
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=150)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE48_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Contact audit failed: '+p.stdout[-400:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
