#!/usr/bin/env python3
"""Read-only SQL and source diagnosis for repeated public sitemap URLs."""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
p=$(mktemp /tmp/storefront-sitemap-check-XXXXXX.php)
trap 'rm -f "$p"' EXIT
cat > "$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$db=new waModel();
$slugs=array(
  'podstole',
  'stul-float-bambuk',
  'shkaf-nwt-0714-beloe-derevo-seryy_1',
  'tumba-s-vydvizhnoy-sektsiey-edis-r-edms-120-dub-denver-temnyy-mokko-430kh690kh1200-mm',
  'fasad-oslo-v6pd-karbon-pepel'
);
foreach($slugs as $slug){
  if(!preg_match('/^[a-z0-9_-]+$/',$slug))throw new Exception('Invalid probe slug');
  $rows=$db->query("SELECT id,url,status,category_id FROM shop_product WHERE url='".$slug."' LIMIT 20")->fetchAll();
  echo 'SITEMAP_COLLISION='.json_encode(array('slug'=>$slug,'rows'=>$rows),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
}
$folders=array($root.'/wa-apps/shop/lib/actions/frontend',$root.'/wa-apps/shop/lib/classes',$root.'/wa-apps/shop/lib/config');
$found=array();
foreach($folders as $folder){
 if(!is_dir($folder))continue;
 $iterator=new RecursiveIteratorIterator(new RecursiveDirectoryIterator($folder,FilesystemIterator::SKIP_DOTS));
 foreach($iterator as $f){
  if(!$f->isFile())continue;
  if(stripos($f->getFilename(),'sitemap')!==false){
   $found[]=str_replace($root.'/','',$f->getPathname());
  }
  if(count($found)>=30)break;
 }
}
echo 'SITEMAP_IMPLEMENTATION_FILES='.json_encode($found,JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php -d display_errors=1 -d log_errors=0 '$p'"
"""
def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('NETANGELS_API_KEY missing')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels token missing')
 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage27-sitemap-read-') as d:
  private=d+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',private],check=True)
  pub=Path(private+'.pub').read_text().strip()
  resp=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'stage27-sitemap-'+os.getenv('GITHUB_RUN_ID','audit')}).encode(),headers)
  kid=resp.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',private,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],input=REMOTE,text=True,capture_output=True,timeout=150)
   for line in p.stdout.splitlines():
    if line.startswith('SITEMAP_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('Sitemap audit failed: '+p.stdout[-400:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH cleanup needs inspection')
if __name__=='__main__':run()
