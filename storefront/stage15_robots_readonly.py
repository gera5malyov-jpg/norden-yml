#!/usr/bin/env python3
"""Stage 15: read-only Webasyst robots generation and Sitemap settings audit.

Only inspects site app robots configuration and domain metadata; no writes.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID, VM_IP, request_json

REMOTE=r"""set -eu
python3 - <<'PY'
import pathlib,json
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
base=root/'wa-apps/site/lib'
found=[]
if base.exists():
 for p in base.rglob('*.php'):
  if 'robot' in p.name.lower():
   lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
   found.append({'file':str(p.relative_to(root)),'lines':[{'n':i+1,'text':l[:350]} for i,l in enumerate(lines[:180])]})
print('SEO_ROBOTS_PHP='+json.dumps(found[:8],ensure_ascii=False))
PY
p=$(mktemp /tmp/seo-robots-read-XXXXXX.php)
trap 'rm -f "$p"' EXIT
cat >"$p" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('site');
$m=new waModel();
$out=array();
foreach(array('site_domain','site_settings','site_route') as $table){
 try{
  $cols=$m->query("SHOW COLUMNS FROM ".$table)->fetchAll();
  $names=array();
  foreach($cols as $c){$names[]=$c['Field'];}
  $out[$table]=array('columns'=>$names);
  if($table==='site_domain'){
   $allow=array_intersect(array('id','name','title'),$names);
   if($allow){
    $out[$table]['domains']=$m->query("SELECT ".implode(',',$allow)." FROM site_domain LIMIT 25")->fetchAll();
   }
  }
 }catch(Throwable $e){$out[$table]['error']=get_class($e);}
}
try{
 $out['robots_settings']=$m->query("SELECT app_id,name,CHAR_LENGTH(value) AS value_length FROM wa_app_settings WHERE (name LIKE '%robot%' OR name LIKE '%sitemap%') AND app_id='site' LIMIT 50")->fetchAll();
}catch(Throwable $e){$out['robots_settings_error']=get_class($e);}
echo 'SEO_ROBOTS_DB='.json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
?>
PHP
chmod 644 "$p"
su -s /bin/bash web -c "php '$p'"
"""

def run():
  key=os.environ.get('NETANGELS_API_KEY','').strip()
  if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
  token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
      urllib.parse.urlencode({'api_key':key}).encode(),
      {'Content-Type':'application/x-www-form-urlencoded'}).get('token')
  if not token:raise RuntimeError('NetAngels gateway token missing')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
  with tempfile.TemporaryDirectory(prefix='storefront-seo-robots-') as td:
    ssh=td+'/id'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
    pub=Path(ssh+'.pub').read_text().strip()
    entry=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),
        'POST',json.dumps({'key':pub,'name':'storefront-seo-robots-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),headers)
    kid=entry.get('id')
    if not kid:raise RuntimeError('Unable to register temporary SSH key')
    try:
      r=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
            '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
            input=REMOTE,text=True,capture_output=True,timeout=160)
      for line in r.stdout.splitlines():
        if line.startswith('SEO_ROBOTS_'):print(line,flush=True)
      if r.returncode:
        raise RuntimeError('Storefront UX fix failed '+r.stdout[-1200:]+' '+r.stderr[-500:])
    finally:
      try:
        request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,headers)
        print('temporary_ssh_key_removed=true',flush=True)
      except Exception:
        print('WARNING: temporary SSH cleanup needs review')
if __name__=='__main__':run()
